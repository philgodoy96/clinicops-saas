import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clinicops.authentication.models import AuthSession
from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import Argon2PasswordHasher
from clinicops.main import app
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)

PASSWORD = "Correct-Horse-Battery-Staple-2026!"


@dataclass(frozen=True, slots=True)
class CommittedUser:
    """Committed user available to independent HTTP sessions."""

    id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class MembershipAdministrationScenario:
    """Committed tenant membership state for HTTP races."""

    tenant_id: UUID
    owner: CommittedUser
    admin: CommittedUser
    staff: CommittedUser
    owner_membership_id: UUID
    admin_membership_id: UUID
    staff_membership_id: UUID


class MembershipAdministrationConcurrencyData:
    """Create, inspect, and remove committed concurrency state."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._tenant_ids: set[UUID] = set()
        self._tracked_emails: set[str] = set()
        self._password_hash = Argon2PasswordHasher().hash(PASSWORD)

    def create_scenario(
        self,
    ) -> MembershipAdministrationScenario:
        """Create one active tenant with owner, admin, and staff."""

        tenant = Tenant(
            name=(f"Membership Concurrency Clinic {uuid4().hex[:8]}"),
            status=TenantStatus.ACTIVE,
        )
        owner = self._create_user(prefix="concurrency-owner")
        admin = self._create_user(prefix="concurrency-admin")
        staff = self._create_user(prefix="concurrency-staff")
        owner_membership = Membership(
            tenant=tenant,
            user=owner,
            role=TenantRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        admin_membership = Membership(
            tenant=tenant,
            user=admin,
            role=TenantRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )
        staff_membership = Membership(
            tenant=tenant,
            user=staff,
            role=TenantRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add_all(
            [
                tenant,
                owner_membership,
                admin_membership,
                staff_membership,
            ]
        )
        self._session.flush()

        scenario = MembershipAdministrationScenario(
            tenant_id=tenant.id,
            owner=CommittedUser(
                id=owner.id,
                email=owner.email,
            ),
            admin=CommittedUser(
                id=admin.id,
                email=admin.email,
            ),
            staff=CommittedUser(
                id=staff.id,
                email=staff.email,
            ),
            owner_membership_id=owner_membership.id,
            admin_membership_id=admin_membership.id,
            staff_membership_id=staff_membership.id,
        )
        self._tenant_ids.add(tenant.id)
        self._session.commit()

        return scenario

    def membership_state(
        self,
        membership_id: UUID,
    ) -> tuple[TenantRole, MembershipStatus] | None:
        """Read the current role and status of one membership."""

        self._session.expire_all()
        membership = self._session.get(
            Membership,
            membership_id,
        )

        if membership is None:
            return None

        return membership.role, membership.status

    def active_owner_user_ids(
        self,
        tenant_id: UUID,
    ) -> set[UUID]:
        """Return active owner identities for one tenant."""

        self._session.expire_all()
        statement = select(Membership.user_id).where(
            Membership.tenant_id == tenant_id,
            Membership.role == TenantRole.OWNER,
            Membership.status == MembershipStatus.ACTIVE,
        )

        return set(self._session.scalars(statement).all())

    def active_owner_count(self, tenant_id: UUID) -> int:
        """Count active owner memberships for one tenant."""

        self._session.expire_all()
        statement = (
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.role == TenantRole.OWNER,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )

        return int(self._session.scalar(statement) or 0)

    def cleanup(self) -> None:
        """Remove all committed state created by this fixture."""

        self._session.rollback()

        users = (
            self._session.scalars(select(User).where(User.email.in_(self._tracked_emails))).all()
            if self._tracked_emails
            else []
        )
        user_ids = {user.id for user in users}

        if user_ids:
            authentication_sessions = self._session.scalars(
                select(AuthSession).where(AuthSession.user_id.in_(user_ids))
            ).all()

            for authentication_session in authentication_sessions:
                self._session.delete(authentication_session)

        if self._tenant_ids:
            memberships = self._session.scalars(
                select(Membership).where(Membership.tenant_id.in_(self._tenant_ids))
            ).all()

            for membership in memberships:
                self._session.delete(membership)

            tenants = self._session.scalars(
                select(Tenant).where(Tenant.id.in_(self._tenant_ids))
            ).all()

            for tenant in tenants:
                self._session.delete(tenant)

        for user in users:
            self._session.delete(user)

        self._session.commit()

    def _create_user(self, *, prefix: str) -> User:
        """Create one active user with a valid password credential."""

        email = f"{prefix}-{uuid4().hex}@example.com"
        user = User(
            email=email,
            status=UserStatus.ACTIVE,
            password_credential=PasswordCredential(
                password_hash=self._password_hash,
            ),
        )
        self._session.add(user)
        self._session.flush()
        self._tracked_emails.add(email)

        return user


@pytest.fixture
def membership_concurrency_data() -> Iterator[MembershipAdministrationConcurrencyData]:
    """Provide committed PostgreSQL state for HTTP races."""

    session = Session(get_engine())
    data = MembershipAdministrationConcurrencyData(session)

    try:
        yield data
    finally:
        data.cleanup()
        session.close()


def login_headers(
    client: TestClient,
    user: CommittedUser,
) -> dict[str, str]:
    """Authenticate one committed user through the real API."""

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": PASSWORD,
        },
    )

    assert response.status_code == 200

    return {"Authorization": (f"Bearer {response.json()['access_token']}")}


async def change_role_and_disable(
    *,
    scenario: MembershipAdministrationScenario,
    owner_headers: dict[str, str],
) -> tuple[Response, Response]:
    """Race role promotion against membership disabling."""

    transport = ASGITransport(
        app=app,
        raise_app_exceptions=False,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        role_response, disable_response = await asyncio.gather(
            client.patch(
                (
                    f"/api/v1/tenants/{scenario.tenant_id}/"
                    f"memberships/{scenario.staff_membership_id}/"
                    "role"
                ),
                headers=owner_headers,
                json={"role": "admin"},
            ),
            client.post(
                (
                    f"/api/v1/tenants/{scenario.tenant_id}/"
                    f"memberships/{scenario.staff_membership_id}/"
                    "disable"
                ),
                headers=owner_headers,
            ),
        )

    return role_response, disable_response


async def transfer_to_two_members(
    *,
    scenario: MembershipAdministrationScenario,
    owner_headers: dict[str, str],
) -> tuple[Response, Response]:
    """Race two ownership transfers from the same owner state."""

    transport = ASGITransport(
        app=app,
        raise_app_exceptions=False,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        first, second = await asyncio.gather(
            client.post(
                (f"/api/v1/tenants/{scenario.tenant_id}/ownership/transfer"),
                headers=owner_headers,
                json={"new_owner_user_id": str(scenario.admin.id)},
            ),
            client.post(
                (f"/api/v1/tenants/{scenario.tenant_id}/ownership/transfer"),
                headers=owner_headers,
                json={"new_owner_user_id": str(scenario.staff.id)},
            ),
        )

    return first, second


async def remove_and_transfer_target(
    *,
    scenario: MembershipAdministrationScenario,
    owner_headers: dict[str, str],
) -> tuple[Response, Response]:
    """Race target removal against ownership promotion."""

    transport = ASGITransport(
        app=app,
        raise_app_exceptions=False,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        removal, transfer = await asyncio.gather(
            client.delete(
                (
                    f"/api/v1/tenants/{scenario.tenant_id}/"
                    f"memberships/{scenario.admin_membership_id}"
                ),
                headers=owner_headers,
            ),
            client.post(
                (f"/api/v1/tenants/{scenario.tenant_id}/ownership/transfer"),
                headers=owner_headers,
                json={"new_owner_user_id": str(scenario.admin.id)},
            ),
        )

    return removal, transfer


def test_role_change_and_disable_produce_serializable_state(
    client: TestClient,
    membership_concurrency_data: (MembershipAdministrationConcurrencyData),
) -> None:
    """Role and status races must match one serial execution order."""

    scenario = membership_concurrency_data.create_scenario()
    owner_headers = login_headers(
        client,
        scenario.owner,
    )

    role_response, disable_response = asyncio.run(
        change_role_and_disable(
            scenario=scenario,
            owner_headers=owner_headers,
        )
    )

    assert disable_response.status_code == 200
    assert role_response.status_code in {200, 409}

    final_state = membership_concurrency_data.membership_state(scenario.staff_membership_id)
    assert final_state is not None
    final_role, final_status = final_state

    assert final_status is MembershipStatus.DISABLED

    if role_response.status_code == 200:
        assert final_role is TenantRole.ADMIN
    else:
        assert role_response.json()["code"] == ("membership_disabled")
        assert final_role is TenantRole.STAFF


def test_concurrent_ownership_transfers_commit_one_owner(
    client: TestClient,
    membership_concurrency_data: (MembershipAdministrationConcurrencyData),
) -> None:
    """Only one transfer may commit from the original owner state."""

    scenario = membership_concurrency_data.create_scenario()
    owner_headers = login_headers(
        client,
        scenario.owner,
    )

    responses = asyncio.run(
        transfer_to_two_members(
            scenario=scenario,
            owner_headers=owner_headers,
        )
    )

    successful = [response for response in responses if response.status_code == 200]
    failed = [response for response in responses if response.status_code != 200]

    assert len(successful) == 1
    assert len(failed) == 1
    assert failed[0].status_code in {403, 409}
    assert failed[0].json()["code"] in {
        "tenant_permission_denied",
        "tenant_ownership_conflict",
    }

    new_owner_user_id = UUID(successful[0].json()["new_owner_user_id"])

    assert new_owner_user_id in {
        scenario.admin.id,
        scenario.staff.id,
    }
    assert membership_concurrency_data.active_owner_user_ids(scenario.tenant_id) == {
        new_owner_user_id
    }
    assert membership_concurrency_data.active_owner_count(scenario.tenant_id) == 1
    assert membership_concurrency_data.membership_state(scenario.owner_membership_id) == (
        TenantRole.ADMIN,
        MembershipStatus.ACTIVE,
    )


def test_removal_and_ownership_transfer_preserve_owner_invariant(
    client: TestClient,
    membership_concurrency_data: (MembershipAdministrationConcurrencyData),
) -> None:
    """Removal and promotion cannot leave contradictory state."""

    scenario = membership_concurrency_data.create_scenario()
    owner_headers = login_headers(
        client,
        scenario.owner,
    )

    removal, transfer = asyncio.run(
        remove_and_transfer_target(
            scenario=scenario,
            owner_headers=owner_headers,
        )
    )

    assert membership_concurrency_data.active_owner_count(scenario.tenant_id) == 1

    if transfer.status_code == 200:
        assert removal.status_code == 409
        assert removal.json()["code"] == ("membership_owner_protected")
        assert membership_concurrency_data.active_owner_user_ids(scenario.tenant_id) == {
            scenario.admin.id
        }
        assert membership_concurrency_data.membership_state(scenario.admin_membership_id) == (
            TenantRole.OWNER,
            MembershipStatus.ACTIVE,
        )
        assert membership_concurrency_data.membership_state(scenario.owner_membership_id) == (
            TenantRole.ADMIN,
            MembershipStatus.ACTIVE,
        )
    else:
        assert removal.status_code == 204
        assert transfer.status_code == 404
        assert transfer.json()["code"] == "membership_not_found"
        assert membership_concurrency_data.membership_state(scenario.admin_membership_id) is None
        assert membership_concurrency_data.active_owner_user_ids(scenario.tenant_id) == {
            scenario.owner.id
        }
        assert membership_concurrency_data.membership_state(scenario.owner_membership_id) == (
            TenantRole.OWNER,
            MembershipStatus.ACTIVE,
        )
