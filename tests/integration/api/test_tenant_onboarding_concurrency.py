import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
)
from clinicops.audit.models import AuditLogEntry
from clinicops.authentication.models import AuthSession
from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import Argon2PasswordHasher
from clinicops.invitations.models import (
    Invitation,
    InvitationStatus,
)
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


class OnboardingConcurrencyData:
    """Create and remove committed onboarding race-test state."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._tenant_ids: set[UUID] = set()
        self._tracked_emails: set[str] = set()
        self._password_hash = Argon2PasswordHasher().hash(PASSWORD)

    def create_user(
        self,
        *,
        email_prefix: str,
    ) -> CommittedUser:
        email = f"{email_prefix}-{uuid4().hex}@example.com"
        user = User(
            email=email,
            status=UserStatus.ACTIVE,
            password_credential=PasswordCredential(
                password_hash=self._password_hash,
            ),
        )
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        self._tracked_emails.add(user.email)

        return CommittedUser(
            id=user.id,
            email=user.email,
        )

    def track_email(self, email: str) -> None:
        self._tracked_emails.add(email)

    def track_tenant(self, tenant_id: UUID) -> None:
        self._tenant_ids.add(tenant_id)

    def user_id_for_email(self, email: str) -> UUID:
        self._session.expire_all()
        user = self._session.scalar(select(User).where(User.email == email))
        assert user is not None

        return user.id

    def user_count_for_email(self, email: str) -> int:
        self._session.expire_all()

        statement = select(func.count()).select_from(User).where(User.email == email)

        return int(self._session.scalar(statement) or 0)

    def membership_count(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> int:
        self._session.expire_all()

        statement = (
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
            )
        )

        return int(self._session.scalar(statement) or 0)

    def active_owner_count(self, tenant_id: UUID) -> int:
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

    def tenant_status(self, tenant_id: UUID) -> TenantStatus:
        self._session.expire_all()
        tenant = self._session.get(Tenant, tenant_id)
        assert tenant is not None

        return tenant.status

    def invitation_status(
        self,
        invitation_id: UUID,
    ) -> InvitationStatus:
        self._session.expire_all()
        invitation = self._session.get(
            Invitation,
            invitation_id,
        )
        assert invitation is not None

        return invitation.status

    def cleanup(self) -> None:
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
            self._session.execute(
                delete(AuditLogEntry).where(AuditLogEntry.tenant_id.in_(self._tenant_ids))
            )

            invitations = self._session.scalars(
                select(Invitation).where(Invitation.tenant_id.in_(self._tenant_ids))
            ).all()

            for invitation in invitations:
                self._session.delete(invitation)

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


@pytest.fixture
def onboarding_concurrency_data() -> Iterator[OnboardingConcurrencyData]:
    session = Session(get_engine())
    data = OnboardingConcurrencyData(session)

    try:
        yield data
    finally:
        data.cleanup()
        session.close()


def login_headers(
    client: TestClient,
    user: CommittedUser,
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": PASSWORD,
        },
    )

    assert response.status_code == 200

    return {"Authorization": (f"Bearer {response.json()['access_token']}")}


def create_tenant(
    client: TestClient,
    *,
    headers: dict[str, str],
    data: OnboardingConcurrencyData,
    name: str,
) -> UUID:
    response = client.post(
        "/api/v1/tenants",
        headers=headers,
        json={"name": name},
    )

    assert response.status_code == 201
    tenant_id = UUID(response.json()["id"])
    data.track_tenant(tenant_id)

    return tenant_id


def issue_invitation(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
    invited_email: str,
) -> tuple[UUID, str]:
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/invitations",
        headers=headers,
        json={
            "invited_email": invited_email,
            "role": "staff",
        },
    )

    assert response.status_code == 201

    return (
        UUID(response.json()["id"]),
        response.json()["token"],
    )


async def accept_invitation_twice(
    *,
    token: str,
    password: str,
) -> tuple[Response, Response]:
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
                "/api/v1/invitations/accept",
                json={
                    "token": token,
                    "password": password,
                },
            ),
            client.post(
                "/api/v1/invitations/accept",
                json={
                    "token": token,
                    "password": password,
                },
            ),
        )

    return first, second


async def create_two_tenants(
    *,
    headers: dict[str, str],
) -> tuple[Response, Response]:
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
                "/api/v1/tenants",
                headers=headers,
                json={
                    "name": "Concurrent North Clinic",
                },
            ),
            client.post(
                "/api/v1/tenants",
                headers=headers,
                json={
                    "name": "Concurrent South Clinic",
                },
            ),
        )

    return first, second


async def accept_and_revoke(
    *,
    tenant_id: UUID,
    invitation_id: UUID,
    token: str,
    owner_headers: dict[str, str],
) -> tuple[Response, Response]:
    transport = ASGITransport(
        app=app,
        raise_app_exceptions=False,
    )

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        acceptance, revocation = await asyncio.gather(
            client.post(
                "/api/v1/invitations/accept",
                json={"token": token},
            ),
            client.post(
                (f"/api/v1/tenants/{tenant_id}/invitations/{invitation_id}/revoke"),
                headers=owner_headers,
            ),
        )

    return acceptance, revocation


def test_concurrent_invitation_acceptance_creates_one_user_and_membership(
    client: TestClient,
    onboarding_concurrency_data: OnboardingConcurrencyData,
) -> None:
    owner = onboarding_concurrency_data.create_user(email_prefix="concurrent-accept-owner")
    owner_headers = login_headers(client, owner)
    tenant_id = create_tenant(
        client,
        headers=owner_headers,
        data=onboarding_concurrency_data,
        name="Concurrent Acceptance Clinic",
    )
    invited_email = f"concurrent-recipient-{uuid4().hex}@example.com"
    onboarding_concurrency_data.track_email(invited_email)
    invitation_id, token = issue_invitation(
        client,
        tenant_id=tenant_id,
        headers=owner_headers,
        invited_email=invited_email,
    )

    responses = asyncio.run(
        accept_invitation_twice(
            token=token,
            password=PASSWORD,
        )
    )

    assert sorted(response.status_code for response in responses) == [200, 409]

    successful_response = next(response for response in responses if response.status_code == 200)
    conflict_response = next(response for response in responses if response.status_code == 409)

    assert successful_response.json()["invitation_id"] == str(invitation_id)
    assert successful_response.json()["user_was_created"] is True
    assert conflict_response.json()["code"] == ("invitation_already_accepted")

    invited_user_id = onboarding_concurrency_data.user_id_for_email(invited_email)

    assert onboarding_concurrency_data.user_count_for_email(invited_email) == 1
    assert (
        onboarding_concurrency_data.membership_count(
            tenant_id=tenant_id,
            user_id=invited_user_id,
        )
        == 1
    )
    assert onboarding_concurrency_data.invitation_status(invitation_id) is InvitationStatus.ACCEPTED


def test_concurrent_tenant_creation_preserves_one_owner_per_tenant(
    client: TestClient,
    onboarding_concurrency_data: OnboardingConcurrencyData,
) -> None:
    owner = onboarding_concurrency_data.create_user(email_prefix="concurrent-tenant-owner")
    owner_headers = login_headers(client, owner)

    responses = asyncio.run(create_two_tenants(headers=owner_headers))

    for response in responses:
        if response.status_code == 201:
            onboarding_concurrency_data.track_tenant(UUID(response.json()["id"]))

    assert [response.status_code for response in responses] == [201, 201]

    tenant_ids = {UUID(response.json()["id"]) for response in responses}

    assert len(tenant_ids) == 2

    for response in responses:
        tenant_id = UUID(response.json()["id"])

        assert response.json()["owner_user_id"] == str(owner.id)
        assert onboarding_concurrency_data.tenant_status(tenant_id) is TenantStatus.ACTIVE
        assert onboarding_concurrency_data.active_owner_count(tenant_id) == 1
        assert (
            onboarding_concurrency_data.membership_count(
                tenant_id=tenant_id,
                user_id=owner.id,
            )
            == 1
        )


def test_acceptance_and_revocation_produce_one_terminal_http_result(
    client: TestClient,
    onboarding_concurrency_data: OnboardingConcurrencyData,
) -> None:
    owner = onboarding_concurrency_data.create_user(email_prefix="terminal-state-owner")
    recipient = onboarding_concurrency_data.create_user(email_prefix="terminal-state-recipient")
    owner_headers = login_headers(client, owner)
    tenant_id = create_tenant(
        client,
        headers=owner_headers,
        data=onboarding_concurrency_data,
        name="Terminal State Clinic",
    )
    invitation_id, token = issue_invitation(
        client,
        tenant_id=tenant_id,
        headers=owner_headers,
        invited_email=recipient.email,
    )

    acceptance, revocation = asyncio.run(
        accept_and_revoke(
            tenant_id=tenant_id,
            invitation_id=invitation_id,
            token=token,
            owner_headers=owner_headers,
        )
    )

    assert sorted(
        [
            acceptance.status_code,
            revocation.status_code,
        ]
    ) == [200, 409]

    invitation_status = onboarding_concurrency_data.invitation_status(invitation_id)
    membership_count = onboarding_concurrency_data.membership_count(
        tenant_id=tenant_id,
        user_id=recipient.id,
    )

    if acceptance.status_code == 200:
        assert revocation.json()["code"] == ("invitation_already_accepted")
        assert invitation_status is InvitationStatus.ACCEPTED
        assert membership_count == 1
    else:
        assert acceptance.json()["code"] == ("invitation_revoked")
        assert revocation.status_code == 200
        assert invitation_status is InvitationStatus.REVOKED
        assert membership_count == 0


def assert_public_audit_item(item: dict[str, Any]) -> None:
    """Reject sensitive fields from public audit API payloads."""

    assert "idempotency_key" not in item
    metadata = item["metadata"]
    assert isinstance(metadata, dict)

    for forbidden in (
        "token",
        "token_hash",
        "password",
        "password_hash",
        "provider_customer_id",
        "provider_subscription_id",
        "raw_payload",
        "idempotency_key",
    ):
        assert forbidden not in metadata


def test_tenant_creation_is_readable_through_audit_api(
    client: TestClient,
    onboarding_concurrency_data: OnboardingConcurrencyData,
) -> None:
    owner = onboarding_concurrency_data.create_user(email_prefix="audit-create-owner")
    owner_headers = login_headers(client, owner)
    tenant_name = f"  Audit Readable Clinic {uuid4().hex[:8]}  "

    create_response = client.post(
        "/api/v1/tenants",
        headers=owner_headers,
        json={"name": tenant_name},
    )

    assert create_response.status_code == 201

    payload = create_response.json()
    tenant_id = UUID(payload["id"])
    onboarding_concurrency_data.track_tenant(tenant_id)
    request_id = create_response.headers[REQUEST_ID_HEADER]
    correlation_id = create_response.headers[CORRELATION_ID_HEADER]

    audit_response = client.get(
        f"/api/v1/tenants/{tenant_id}/audit-logs",
        headers=owner_headers,
    )

    assert audit_response.status_code == 200

    body = audit_response.json()
    items = body["items"]

    assert body["next_cursor"] is None
    assert len(items) == 1

    item = items[0]

    assert item["action"] == "tenant.created"
    assert item["resource"] == {
        "type": "tenant",
        "id": str(tenant_id),
    }
    assert item["actor"] == {
        "type": "user",
        "user_id": str(owner.id),
        "role": TenantRole.OWNER.value,
    }
    assert item["source"] == "http"
    assert item["request_id"] == request_id
    assert item["correlation_id"] == correlation_id
    assert item["metadata"] == {
        "tenant_name": payload["name"],
    }
    assert_public_audit_item(item)


def test_invitation_issue_and_accept_appear_in_audit_timeline(
    client: TestClient,
    onboarding_concurrency_data: OnboardingConcurrencyData,
) -> None:
    owner = onboarding_concurrency_data.create_user(email_prefix="audit-invite-owner")
    owner_headers = login_headers(client, owner)
    tenant_id = create_tenant(
        client,
        headers=owner_headers,
        data=onboarding_concurrency_data,
        name="Audit Invitation Lifecycle Clinic",
    )
    invited_email = f"audit-invite-recipient-{uuid4().hex}@example.com"
    onboarding_concurrency_data.track_email(invited_email)

    invitation_id, token = issue_invitation(
        client,
        tenant_id=tenant_id,
        headers=owner_headers,
        invited_email=invited_email,
    )

    accept_response = client.post(
        "/api/v1/invitations/accept",
        json={
            "token": token,
            "password": PASSWORD,
        },
    )

    assert accept_response.status_code == 200

    accepted_user_id = accept_response.json()["user_id"]
    accept_request_id = accept_response.headers[REQUEST_ID_HEADER]
    accept_correlation_id = accept_response.headers[CORRELATION_ID_HEADER]

    audit_response = client.get(
        f"/api/v1/tenants/{tenant_id}/audit-logs",
        headers=owner_headers,
        params={
            "resource_type": "invitation",
            "resource_id": str(invitation_id),
        },
    )

    assert audit_response.status_code == 200

    body = audit_response.json()
    items = body["items"]

    assert body["next_cursor"] is None
    assert len(items) == 2
    assert [item["action"] for item in items] == [
        "invitation.accepted",
        "invitation.created",
    ]
    assert (
        items[0]["resource"]
        == items[1]["resource"]
        == {
            "type": "invitation",
            "id": str(invitation_id),
        }
    )

    accepted = items[0]
    created = items[1]

    assert accepted["actor"] == {
        "type": "system",
        "user_id": None,
        "role": None,
    }
    assert accepted["source"] == "http"
    assert accepted["request_id"] == accept_request_id
    assert accepted["correlation_id"] == accept_correlation_id
    assert accepted["metadata"] == {
        "accepted_user_id": accepted_user_id,
        "accepted_role": TenantRole.STAFF.value,
    }
    assert_public_audit_item(accepted)

    assert created["actor"] == {
        "type": "user",
        "user_id": str(owner.id),
        "role": TenantRole.OWNER.value,
    }
    assert created["source"] == "http"
    assert created["request_id"]
    assert created["correlation_id"]
    assert created["metadata"] == {
        "invited_role": TenantRole.STAFF.value,
    }
    assert_public_audit_item(created)
    assert token not in audit_response.text
