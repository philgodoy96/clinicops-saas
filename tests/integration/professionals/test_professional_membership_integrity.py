from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.api.errors import PROBLEM_MEDIA_TYPE
from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
)
from clinicops.audit.actions import AuditAction
from clinicops.audit.models import AuditLogEntry
from clinicops.authentication.models import AuthSession
from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import Argon2PasswordHasher
from clinicops.professionals.models import Professional
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)

PASSWORD = "Correct-Horse-Battery-Staple-2026!"

PROFESSIONAL_COLLECTION_PATH = "/api/v1/tenants/{tenant_id}/professionals"
PROFESSIONAL_ITEM_PATH = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}"
PROFESSIONAL_ARCHIVE_PATH = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/archive"
PROFESSIONAL_RESTORE_PATH = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/restore"
PROFESSIONAL_LINK_PATH = (
    "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/link-membership"
)
PROFESSIONAL_UNLINK_PATH = (
    "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/unlink-membership"
)
MEMBERSHIPS_PATH = "/api/v1/tenants/{tenant_id}/memberships"
MEMBERSHIP_ITEM_PATH = "/api/v1/tenants/{tenant_id}/memberships/{membership_id}"
MEMBERSHIP_DISABLE_PATH = "/api/v1/tenants/{tenant_id}/memberships/{membership_id}/disable"
AUDIT_LOGS_PATH = "/api/v1/tenants/{tenant_id}/audit-logs"

PROFILE_PAYLOAD = {
    "full_name": "Morgan Reed",
    "specialty": "Dentistry",
    "registration_number": "DDS-48291",
    "registration_region": "CA",
    "email": "morgan@example.com",
    "phone": "+1-202-555-0130",
    "external_reference": "PROVIDER-100",
}

FORBIDDEN_PROFILE_VALUES = {
    "Morgan Reed",
    "Dentistry",
    "DDS-48291",
    "CA",
    "morgan@example.com",
    "+1-202-555-0130",
    "PROVIDER-100",
}


@dataclass(frozen=True, slots=True)
class CommittedUser:
    id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class CommittedTenant:
    id: UUID
    owner_membership_id: UUID


@dataclass(frozen=True, slots=True)
class CommittedMembership:
    id: UUID
    user_id: UUID
    role: TenantRole


@dataclass(frozen=True, slots=True)
class MembershipSnapshot:
    id: UUID
    user_id: UUID
    role: TenantRole
    status: MembershipStatus


class ProfessionalMembershipIntegrityFactory:
    """Create committed tenant state and clean it up after API tests."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._user_ids: set[UUID] = set()
        self._tenant_ids: set[UUID] = set()
        self._password_hash = Argon2PasswordHasher().hash(PASSWORD)

    def create_user(self, *, email_prefix: str) -> CommittedUser:
        user = User(
            email=f"{email_prefix}-{uuid4().hex}@example.com",
            status=UserStatus.ACTIVE,
            password_credential=PasswordCredential(
                password_hash=self._password_hash,
            ),
        )
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        self._user_ids.add(user.id)
        return CommittedUser(id=user.id, email=user.email)

    def create_tenant(
        self,
        *,
        owner: CommittedUser,
        name: str,
    ) -> CommittedTenant:
        tenant = Tenant(
            name=f"{name} {uuid4().hex[:8]}",
            status=TenantStatus.ACTIVE,
        )
        owner_membership = Membership(
            tenant=tenant,
            user_id=owner.id,
            role=TenantRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add_all([tenant, owner_membership])
        self._session.commit()
        self._session.refresh(tenant)
        self._session.refresh(owner_membership)
        self._tenant_ids.add(tenant.id)
        return CommittedTenant(
            id=tenant.id,
            owner_membership_id=owner_membership.id,
        )

    def add_membership(
        self,
        *,
        tenant: CommittedTenant,
        user: CommittedUser,
        role: TenantRole,
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> CommittedMembership:
        membership = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            status=status,
        )
        self._session.add(membership)
        self._session.commit()
        self._session.refresh(membership)
        return CommittedMembership(
            id=membership.id,
            user_id=user.id,
            role=role,
        )

    def membership_snapshot(self, membership_id: UUID) -> MembershipSnapshot | None:
        self._session.expire_all()
        membership = self._session.get(Membership, membership_id)
        if membership is None:
            return None
        return MembershipSnapshot(
            id=membership.id,
            user_id=membership.user_id,
            role=membership.role,
            status=membership.status,
        )

    def professional_row(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> Professional | None:
        self._session.expire_all()
        return self._session.execute(
            select(Professional).where(
                Professional.tenant_id == tenant_id,
                Professional.id == professional_id,
            )
        ).scalar_one_or_none()

    def cleanup(self) -> None:
        self._session.rollback()
        self._session.expunge_all()

        if self._user_ids:
            self._session.execute(
                delete(AuthSession)
                .where(AuthSession.user_id.in_(self._user_ids))
                .execution_options(synchronize_session=False)
            )

        if self._tenant_ids:
            self._session.execute(
                delete(AuditLogEntry)
                .where(AuditLogEntry.tenant_id.in_(self._tenant_ids))
                .execution_options(synchronize_session=False)
            )
            self._session.execute(
                delete(Professional)
                .where(Professional.tenant_id.in_(self._tenant_ids))
                .execution_options(synchronize_session=False)
            )
            self._session.execute(
                delete(Membership)
                .where(Membership.tenant_id.in_(self._tenant_ids))
                .execution_options(synchronize_session=False)
            )
            self._session.execute(
                delete(Tenant)
                .where(Tenant.id.in_(self._tenant_ids))
                .execution_options(synchronize_session=False)
            )

        if self._user_ids:
            self._session.execute(
                delete(User)
                .where(User.id.in_(self._user_ids))
                .execution_options(synchronize_session=False)
            )

        self._session.commit()


@pytest.fixture
def integrity_factory() -> Iterator[ProfessionalMembershipIntegrityFactory]:
    session = Session(get_engine())
    factory = ProfessionalMembershipIntegrityFactory(session)
    try:
        yield factory
    finally:
        factory.cleanup()
        session.close()


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated PostgreSQL transaction for constraint tests."""

    session = Session(get_engine())
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def login_headers(
    client: TestClient,
    user: CommittedUser,
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200

    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    if request_id is not None:
        headers[REQUEST_ID_HEADER] = request_id
    if correlation_id is not None:
        headers[CORRELATION_ID_HEADER] = correlation_id
    return headers


def assert_problem(
    response: Response,
    *,
    status_code: int,
    code: str,
) -> dict[str, object]:
    body = cast(dict[str, object], response.json())
    assert response.status_code == status_code
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["status"] == status_code
    assert body["code"] == code
    assert body["type"] == f"urn:clinicops:problem:{code}"
    assert body["request_id"]
    assert body["correlation_id"]
    return body


def _profile_fields(body: dict[str, object]) -> dict[str, object]:
    return {
        "full_name": body["full_name"],
        "specialty": body["specialty"],
        "registration_number": body["registration_number"],
        "registration_region": body["registration_region"],
        "email": body["email"],
        "phone": body["phone"],
        "external_reference": body["external_reference"],
        "status": body["status"],
        "tenant_id": body["tenant_id"],
        "id": body["id"],
    }


def _metadata_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        strings: set[str] = set()
        for item in value:
            strings.update(_metadata_strings(item))
        return strings
    if isinstance(value, dict):
        strings = set()
        for key, item in value.items():
            strings.add(str(key))
            strings.update(_metadata_strings(item))
        return strings
    return set()


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def create_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
) -> dict[str, object]:
    response = client.post(
        PROFESSIONAL_COLLECTION_PATH.format(tenant_id=tenant_id),
        headers=headers,
        json=PROFILE_PAYLOAD,
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def get_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    professional_id: str,
    headers: dict[str, str],
) -> dict[str, object]:
    response = client.get(
        PROFESSIONAL_ITEM_PATH.format(
            tenant_id=tenant_id,
            professional_id=professional_id,
        ),
        headers=headers,
    )
    assert response.status_code == 200
    return cast(dict[str, object], response.json())


def link_membership(
    client: TestClient,
    *,
    tenant_id: UUID,
    professional_id: str,
    membership_id: UUID,
    expected_version: int,
    headers: dict[str, str],
) -> Response:
    return client.post(
        PROFESSIONAL_LINK_PATH.format(
            tenant_id=tenant_id,
            professional_id=professional_id,
        ),
        headers=headers,
        json={
            "membership_id": str(membership_id),
            "expected_version": expected_version,
        },
    )


def unlink_membership(
    client: TestClient,
    *,
    tenant_id: UUID,
    professional_id: str,
    expected_version: int,
    headers: dict[str, str],
) -> Response:
    return client.post(
        PROFESSIONAL_UNLINK_PATH.format(
            tenant_id=tenant_id,
            professional_id=professional_id,
        ),
        headers=headers,
        json={"expected_version": expected_version},
    )


def archive_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    professional_id: str,
    expected_version: int,
    headers: dict[str, str],
) -> dict[str, object]:
    response = client.post(
        PROFESSIONAL_ARCHIVE_PATH.format(
            tenant_id=tenant_id,
            professional_id=professional_id,
        ),
        headers=headers,
        json={"expected_version": expected_version},
    )
    assert response.status_code == 200
    return cast(dict[str, object], response.json())


def restore_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    professional_id: str,
    expected_version: int,
    headers: dict[str, str],
) -> dict[str, object]:
    response = client.post(
        PROFESSIONAL_RESTORE_PATH.format(
            tenant_id=tenant_id,
            professional_id=professional_id,
        ),
        headers=headers,
        json={"expected_version": expected_version},
    )
    assert response.status_code == 200
    return cast(dict[str, object], response.json())


def list_memberships(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
) -> list[dict[str, object]]:
    response = client.get(
        MEMBERSHIPS_PATH.format(tenant_id=tenant_id),
        headers=headers,
    )
    assert response.status_code == 200
    return cast(list[dict[str, object]], response.json()["items"])


def membership_from_list(
    items: list[dict[str, object]],
    membership_id: UUID,
) -> dict[str, object]:
    matches = [item for item in items if item["id"] == str(membership_id)]
    assert len(matches) == 1
    return matches[0]


def fetch_audit_items(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
    **params: str,
) -> list[dict[str, object]]:
    response = client.get(
        AUDIT_LOGS_PATH.format(tenant_id=tenant_id),
        headers=headers,
        params=params,
    )
    assert response.status_code == 200
    return cast(list[dict[str, object]], response.json()["items"])


def professional_membership_audits(
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        item
        for item in items
        if item["action"]
        in {
            AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value,
            AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value,
        }
    ]


def _create_constraint_tenant_and_membership(
    session: Session,
    *,
    name: str,
) -> tuple[Tenant, Membership]:
    tenant = Tenant(name=f"{name} {uuid4().hex[:8]}")
    user = User(email=f"constraint-{uuid4().hex}@example.com")
    membership = Membership(
        tenant=tenant,
        user=user,
        role=TenantRole.STAFF,
    )
    session.add_all([tenant, membership])
    session.flush()
    return tenant, membership


def test_active_professional_can_link_membership_preserving_state_and_audit(
    client: TestClient,
    integrity_factory: ProfessionalMembershipIntegrityFactory,
) -> None:
    owner = integrity_factory.create_user(email_prefix="integrity-link-owner")
    member = integrity_factory.create_user(email_prefix="integrity-link-member")
    tenant = integrity_factory.create_tenant(
        owner=owner,
        name="Integrity Link Clinic",
    )
    membership = integrity_factory.add_membership(
        tenant=tenant,
        user=member,
        role=TenantRole.STAFF,
    )
    headers = login_headers(client, owner)
    created = create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
    )
    professional_id = cast(str, created["id"])
    profile_before = _profile_fields(created)
    membership_before = membership_from_list(
        list_memberships(client, tenant_id=tenant.id, headers=headers),
        membership.id,
    )
    audits_before = professional_membership_audits(
        fetch_audit_items(
            client,
            tenant_id=tenant.id,
            headers=headers,
            resource_type="professional",
            resource_id=professional_id,
        )
    )

    link_response = link_membership(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        membership_id=membership.id,
        expected_version=1,
        headers=headers,
    )
    assert link_response.status_code == 200
    linked = cast(dict[str, object], link_response.json())

    membership_after = membership_from_list(
        list_memberships(client, tenant_id=tenant.id, headers=headers),
        membership.id,
    )
    audits_after = professional_membership_audits(
        fetch_audit_items(
            client,
            tenant_id=tenant.id,
            headers=headers,
            resource_type="professional",
            resource_id=professional_id,
        )
    )
    linked_audits = [
        item
        for item in audits_after
        if item["action"] == AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value
    ]

    assert linked["membership_id"] == str(membership.id)
    assert linked["version"] == 2
    assert created["version"] == 1
    assert _profile_fields(linked) == profile_before
    assert membership_after["role"] == membership_before["role"] == TenantRole.STAFF.value
    assert (
        membership_after["status"] == membership_before["status"] == (MembershipStatus.ACTIVE.value)
    )
    assert membership_after["user_id"] == membership_before["user_id"] == str(member.id)
    assert audits_before == []
    assert len(linked_audits) == 1
    assert linked_audits[0]["metadata"] == {
        "membership_id": str(membership.id),
        "version": 2,
    }
    assert FORBIDDEN_PROFILE_VALUES.isdisjoint(_metadata_strings(linked_audits[0]["metadata"]))


def test_rejected_links_return_safe_errors_without_mutating_or_auditing(
    client: TestClient,
    integrity_factory: ProfessionalMembershipIntegrityFactory,
) -> None:
    owner = integrity_factory.create_user(email_prefix="integrity-reject-owner")
    member_a = integrity_factory.create_user(email_prefix="integrity-reject-a")
    member_b = integrity_factory.create_user(email_prefix="integrity-reject-b")
    foreign_owner = integrity_factory.create_user(email_prefix="integrity-reject-foreign")
    foreign_member = integrity_factory.create_user(email_prefix="integrity-reject-foreign-member")
    tenant = integrity_factory.create_tenant(
        owner=owner,
        name="Integrity Reject Clinic",
    )
    foreign_tenant = integrity_factory.create_tenant(
        owner=foreign_owner,
        name="Integrity Reject Foreign Clinic",
    )
    membership_a = integrity_factory.add_membership(
        tenant=tenant,
        user=member_a,
        role=TenantRole.STAFF,
    )
    membership_b = integrity_factory.add_membership(
        tenant=tenant,
        user=member_b,
        role=TenantRole.ADMIN,
    )
    foreign_membership = integrity_factory.add_membership(
        tenant=foreign_tenant,
        user=foreign_member,
        role=TenantRole.STAFF,
    )
    headers = login_headers(client, owner)

    first = create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
    )
    second_response = client.post(
        PROFESSIONAL_COLLECTION_PATH.format(tenant_id=tenant.id),
        headers=headers,
        json={
            **PROFILE_PAYLOAD,
            "full_name": "Alex Morgan",
            "email": "alex@example.com",
            "external_reference": "PROVIDER-200",
        },
    )
    assert second_response.status_code == 201
    second = cast(dict[str, object], second_response.json())
    first_id = cast(str, first["id"])
    second_id = cast(str, second["id"])

    successful_link = link_membership(
        client,
        tenant_id=tenant.id,
        professional_id=first_id,
        membership_id=membership_a.id,
        expected_version=1,
        headers=headers,
    )
    assert successful_link.status_code == 200
    assert successful_link.json()["version"] == 2

    disable_response = client.post(
        MEMBERSHIP_DISABLE_PATH.format(
            tenant_id=tenant.id,
            membership_id=membership_b.id,
        ),
        headers=headers,
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["disabled_at"] is not None
    assert (
        membership_from_list(
            list_memberships(client, tenant_id=tenant.id, headers=headers),
            membership_b.id,
        )["status"]
        == MembershipStatus.DISABLED.value
    )

    first_before = get_professional(
        client,
        tenant_id=tenant.id,
        professional_id=first_id,
        headers=headers,
    )
    second_before = get_professional(
        client,
        tenant_id=tenant.id,
        professional_id=second_id,
        headers=headers,
    )
    memberships_before = {
        item["id"]: {
            "role": item["role"],
            "status": item["status"],
            "user_id": item["user_id"],
        }
        for item in list_memberships(client, tenant_id=tenant.id, headers=headers)
    }
    foreign_before = integrity_factory.membership_snapshot(foreign_membership.id)
    audits_before = professional_membership_audits(
        fetch_audit_items(
            client,
            tenant_id=tenant.id,
            headers=headers,
            resource_type="professional",
        )
    )
    assert len(audits_before) == 1
    assert audits_before[0]["action"] == AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value

    missing_body = assert_problem(
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=second_id,
            membership_id=uuid4(),
            expected_version=1,
            headers=headers,
        ),
        status_code=404,
        code="professional_membership_not_found",
    )
    foreign_body = assert_problem(
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=second_id,
            membership_id=foreign_membership.id,
            expected_version=1,
            headers=headers,
        ),
        status_code=404,
        code="professional_membership_not_found",
    )
    assert foreign_body["code"] == missing_body["code"]
    assert foreign_body["type"] == missing_body["type"]
    assert foreign_body["detail"] == missing_body["detail"]
    assert foreign_body["title"] == missing_body["title"]
    assert "tenant" not in cast(str, foreign_body["detail"]).lower()

    assert_problem(
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=second_id,
            membership_id=membership_b.id,
            expected_version=1,
            headers=headers,
        ),
        status_code=409,
        code="professional_membership_inactive",
    )
    assert_problem(
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=first_id,
            membership_id=membership_b.id,
            expected_version=2,
            headers=headers,
        ),
        status_code=409,
        code="professional_already_linked",
    )
    assert_problem(
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=second_id,
            membership_id=membership_a.id,
            expected_version=1,
            headers=headers,
        ),
        status_code=409,
        code="professional_membership_link_conflict",
    )

    first_after = get_professional(
        client,
        tenant_id=tenant.id,
        professional_id=first_id,
        headers=headers,
    )
    second_after = get_professional(
        client,
        tenant_id=tenant.id,
        professional_id=second_id,
        headers=headers,
    )
    memberships_after = {
        item["id"]: {
            "role": item["role"],
            "status": item["status"],
            "user_id": item["user_id"],
        }
        for item in list_memberships(client, tenant_id=tenant.id, headers=headers)
    }
    foreign_after = integrity_factory.membership_snapshot(foreign_membership.id)
    audits_after = professional_membership_audits(
        fetch_audit_items(
            client,
            tenant_id=tenant.id,
            headers=headers,
            resource_type="professional",
        )
    )

    assert first_after == first_before
    assert second_after == second_before
    assert memberships_after == memberships_before
    assert foreign_after == foreign_before
    assert audits_after == audits_before


def test_explicit_unlink_lifecycle_and_guards(
    client: TestClient,
    integrity_factory: ProfessionalMembershipIntegrityFactory,
) -> None:
    owner = integrity_factory.create_user(email_prefix="integrity-unlink-owner")
    member = integrity_factory.create_user(email_prefix="integrity-unlink-member")
    tenant = integrity_factory.create_tenant(
        owner=owner,
        name="Integrity Unlink Clinic",
    )
    membership = integrity_factory.add_membership(
        tenant=tenant,
        user=member,
        role=TenantRole.STAFF,
    )
    headers = login_headers(client, owner)
    created = create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
    )
    professional_id = cast(str, created["id"])
    linked = cast(
        dict[str, object],
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=professional_id,
            membership_id=membership.id,
            expected_version=1,
            headers=headers,
        ).json(),
    )
    membership_before = membership_from_list(
        list_memberships(client, tenant_id=tenant.id, headers=headers),
        membership.id,
    )
    profile_before = _profile_fields(linked)

    unlink_response = unlink_membership(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        expected_version=2,
        headers=headers,
    )
    assert unlink_response.status_code == 200
    unlinked = cast(dict[str, object], unlink_response.json())
    membership_after = membership_from_list(
        list_memberships(client, tenant_id=tenant.id, headers=headers),
        membership.id,
    )
    unlink_audits = [
        item
        for item in fetch_audit_items(
            client,
            tenant_id=tenant.id,
            headers=headers,
            resource_type="professional",
            resource_id=professional_id,
        )
        if item["action"] == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
    ]

    assert unlinked["membership_id"] is None
    assert unlinked["version"] == 3
    assert linked["version"] == 2
    assert _profile_fields(unlinked) == profile_before
    assert membership_after == membership_before
    assert integrity_factory.membership_snapshot(membership.id) is not None
    assert len(unlink_audits) == 1
    assert unlink_audits[0]["metadata"] == {
        "membership_id": str(membership.id),
        "reason": "explicit",
        "version": 3,
    }

    assert_problem(
        unlink_membership(
            client,
            tenant_id=tenant.id,
            professional_id=professional_id,
            expected_version=3,
            headers=headers,
        ),
        status_code=409,
        code="professional_not_linked",
    )
    audits_after_rejected = [
        item
        for item in fetch_audit_items(
            client,
            tenant_id=tenant.id,
            headers=headers,
            resource_type="professional",
            resource_id=professional_id,
        )
        if item["action"] == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
    ]
    assert audits_after_rejected == unlink_audits

    relinked = cast(
        dict[str, object],
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=professional_id,
            membership_id=membership.id,
            expected_version=3,
            headers=headers,
        ).json(),
    )
    archived = archive_professional(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        expected_version=cast(int, relinked["version"]),
        headers=headers,
    )
    assert archived["membership_id"] == str(membership.id)
    assert_problem(
        unlink_membership(
            client,
            tenant_id=tenant.id,
            professional_id=professional_id,
            expected_version=cast(int, archived["version"]),
            headers=headers,
        ),
        status_code=409,
        code="professional_already_archived",
    )
    still_archived = get_professional(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        headers=headers,
    )
    assert still_archived["status"] == "archived"
    assert still_archived["membership_id"] == str(membership.id)
    assert still_archived["version"] == archived["version"]


def test_archive_restore_preserve_membership_independent_of_membership_status(
    client: TestClient,
    integrity_factory: ProfessionalMembershipIntegrityFactory,
) -> None:
    owner = integrity_factory.create_user(email_prefix="integrity-lifecycle-owner")
    member = integrity_factory.create_user(email_prefix="integrity-lifecycle-member")
    tenant = integrity_factory.create_tenant(
        owner=owner,
        name="Integrity Lifecycle Clinic",
    )
    membership = integrity_factory.add_membership(
        tenant=tenant,
        user=member,
        role=TenantRole.STAFF,
    )
    headers = login_headers(client, owner)
    created = create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
    )
    professional_id = cast(str, created["id"])
    linked = cast(
        dict[str, object],
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=professional_id,
            membership_id=membership.id,
            expected_version=1,
            headers=headers,
        ).json(),
    )
    archived = archive_professional(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        expected_version=cast(int, linked["version"]),
        headers=headers,
    )
    assert archived["membership_id"] == str(membership.id)
    assert archived["status"] == "archived"

    disable_response = client.post(
        MEMBERSHIP_DISABLE_PATH.format(
            tenant_id=tenant.id,
            membership_id=membership.id,
        ),
        headers=headers,
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["disabled_at"] is not None
    assert (
        membership_from_list(
            list_memberships(client, tenant_id=tenant.id, headers=headers),
            membership.id,
        )["status"]
        == MembershipStatus.DISABLED.value
    )

    restored = restore_professional(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        expected_version=cast(int, archived["version"]),
        headers=headers,
    )
    membership_after_restore = membership_from_list(
        list_memberships(client, tenant_id=tenant.id, headers=headers),
        membership.id,
    )

    assert restored["status"] == "active"
    assert restored["membership_id"] == str(membership.id)
    assert membership_after_restore["status"] == MembershipStatus.DISABLED.value
    assert membership_after_restore["role"] == TenantRole.STAFF.value
    assert membership_after_restore["user_id"] == str(member.id)

    unlink_response = unlink_membership(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        expected_version=cast(int, restored["version"]),
        headers=headers,
    )
    assert unlink_response.status_code == 200
    unlinked = cast(dict[str, object], unlink_response.json())
    membership_after_unlink = membership_from_list(
        list_memberships(client, tenant_id=tenant.id, headers=headers),
        membership.id,
    )

    assert unlinked["membership_id"] is None
    assert unlinked["version"] == cast(int, restored["version"]) + 1
    assert membership_after_unlink["status"] == MembershipStatus.DISABLED.value
    assert membership_after_unlink["user_id"] == str(member.id)


@pytest.mark.parametrize(
    "archive_before_removal",
    [False, True],
    ids=["active_professional", "archived_professional"],
)
def test_membership_removal_unlinks_professional_and_preserves_safe_audits(
    client: TestClient,
    integrity_factory: ProfessionalMembershipIntegrityFactory,
    archive_before_removal: bool,
) -> None:
    owner = integrity_factory.create_user(email_prefix="integrity-removal-owner")
    member = integrity_factory.create_user(email_prefix="integrity-removal-member")
    tenant = integrity_factory.create_tenant(
        owner=owner,
        name="Integrity Removal Clinic",
    )
    membership = integrity_factory.add_membership(
        tenant=tenant,
        user=member,
        role=TenantRole.STAFF,
    )
    request_id = str(uuid4())
    correlation_id = str(uuid4())
    headers = login_headers(
        client,
        owner,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    created = create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
    )
    professional_id = cast(str, created["id"])
    linked = cast(
        dict[str, object],
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=professional_id,
            membership_id=membership.id,
            expected_version=1,
            headers=headers,
        ).json(),
    )
    version_before_removal = cast(int, linked["version"])
    expected_status = "active"
    if archive_before_removal:
        archived = archive_professional(
            client,
            tenant_id=tenant.id,
            professional_id=professional_id,
            expected_version=version_before_removal,
            headers=headers,
        )
        version_before_removal = cast(int, archived["version"])
        expected_status = "archived"

    remove_response = client.delete(
        MEMBERSHIP_ITEM_PATH.format(
            tenant_id=tenant.id,
            membership_id=membership.id,
        ),
        headers=headers,
    )
    assert remove_response.status_code == 204

    persisted = get_professional(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        headers=headers,
    )
    assert integrity_factory.membership_snapshot(membership.id) is None
    assert persisted["id"] == professional_id
    assert persisted["membership_id"] is None
    assert persisted["status"] == expected_status
    assert persisted["version"] == version_before_removal + 1
    assert _profile_fields(persisted) == {
        **_profile_fields(created),
        "status": expected_status,
    }

    professional_audits = fetch_audit_items(
        client,
        tenant_id=tenant.id,
        headers=headers,
        resource_type="professional",
        resource_id=professional_id,
    )
    membership_audits = fetch_audit_items(
        client,
        tenant_id=tenant.id,
        headers=headers,
        resource_type="membership",
        resource_id=str(membership.id),
    )
    unlink_audits = [
        item
        for item in professional_audits
        if item["action"] == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
    ]
    removed_audits = [
        item for item in membership_audits if item["action"] == AuditAction.MEMBERSHIP_REMOVED.value
    ]

    assert len(unlink_audits) == 1
    assert len(removed_audits) == 1
    unlink_audit = unlink_audits[0]
    removed_audit = removed_audits[0]
    assert unlink_audit["metadata"] == {
        "membership_id": str(membership.id),
        "reason": "membership_removal",
        "version": persisted["version"],
    }
    assert unlink_audit["tenant_id"] == removed_audit["tenant_id"] == str(tenant.id)
    unlink_actor = cast(dict[str, object], unlink_audit["actor"])
    removed_actor = cast(dict[str, object], removed_audit["actor"])
    assert unlink_actor["user_id"] == removed_actor["user_id"] == str(owner.id)
    assert unlink_audit["request_id"] == removed_audit["request_id"] == request_id
    assert unlink_audit["correlation_id"] == removed_audit["correlation_id"] == (correlation_id)
    assert FORBIDDEN_PROFILE_VALUES.isdisjoint(_metadata_strings(unlink_audit["metadata"]))
    assert FORBIDDEN_PROFILE_VALUES.isdisjoint(_metadata_strings(removed_audit["metadata"]))


def test_unlinked_and_rejected_membership_removal_leave_professional_untouched(
    client: TestClient,
    integrity_factory: ProfessionalMembershipIntegrityFactory,
) -> None:
    owner = integrity_factory.create_user(email_prefix="integrity-reject-remove-owner")
    admin = integrity_factory.create_user(email_prefix="integrity-reject-remove-admin")
    member = integrity_factory.create_user(email_prefix="integrity-reject-remove-member")
    foreign_owner = integrity_factory.create_user(email_prefix="integrity-reject-remove-foreign")
    tenant = integrity_factory.create_tenant(
        owner=owner,
        name="Integrity Reject Removal Clinic",
    )
    foreign_tenant = integrity_factory.create_tenant(
        owner=foreign_owner,
        name="Integrity Reject Removal Foreign Clinic",
    )
    admin_membership = integrity_factory.add_membership(
        tenant=tenant,
        user=admin,
        role=TenantRole.ADMIN,
    )
    linked_membership = integrity_factory.add_membership(
        tenant=tenant,
        user=member,
        role=TenantRole.STAFF,
    )
    unlinked_user = integrity_factory.create_user(email_prefix="integrity-reject-remove-unlinked")
    unlinked_membership = integrity_factory.add_membership(
        tenant=tenant,
        user=unlinked_user,
        role=TenantRole.STAFF,
    )
    owner_headers = login_headers(client, owner)
    admin_headers = login_headers(client, admin)

    created = create_professional(
        client,
        tenant_id=tenant.id,
        headers=owner_headers,
    )
    professional_id = cast(str, created["id"])
    linked = cast(
        dict[str, object],
        link_membership(
            client,
            tenant_id=tenant.id,
            professional_id=professional_id,
            membership_id=linked_membership.id,
            expected_version=1,
            headers=owner_headers,
        ).json(),
    )

    remove_unlinked = client.delete(
        MEMBERSHIP_ITEM_PATH.format(
            tenant_id=tenant.id,
            membership_id=unlinked_membership.id,
        ),
        headers=owner_headers,
    )
    assert remove_unlinked.status_code == 204
    assert integrity_factory.membership_snapshot(unlinked_membership.id) is None

    audits_after_unlinked_removal = professional_membership_audits(
        fetch_audit_items(
            client,
            tenant_id=tenant.id,
            headers=owner_headers,
            resource_type="professional",
            resource_id=professional_id,
        )
    )
    assert [item["action"] for item in audits_after_unlinked_removal] == [
        AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value
    ]

    before_rejected = get_professional(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        headers=owner_headers,
    )
    assert before_rejected["membership_id"] == str(linked_membership.id)
    assert before_rejected["version"] == linked["version"]

    assert_problem(
        client.delete(
            MEMBERSHIP_ITEM_PATH.format(
                tenant_id=tenant.id,
                membership_id=tenant.owner_membership_id,
            ),
            headers=owner_headers,
        ),
        status_code=409,
        code="membership_self_management_not_allowed",
    )
    assert_problem(
        client.delete(
            MEMBERSHIP_ITEM_PATH.format(
                tenant_id=tenant.id,
                membership_id=tenant.owner_membership_id,
            ),
            headers=admin_headers,
        ),
        status_code=409,
        code="membership_owner_protected",
    )
    assert_problem(
        client.delete(
            MEMBERSHIP_ITEM_PATH.format(
                tenant_id=tenant.id,
                membership_id=admin_membership.id,
            ),
            headers=admin_headers,
        ),
        status_code=409,
        code="membership_self_management_not_allowed",
    )
    assert_problem(
        client.delete(
            MEMBERSHIP_ITEM_PATH.format(
                tenant_id=tenant.id,
                membership_id=uuid4(),
            ),
            headers=owner_headers,
        ),
        status_code=404,
        code="membership_not_found",
    )
    assert_problem(
        client.delete(
            MEMBERSHIP_ITEM_PATH.format(
                tenant_id=foreign_tenant.id,
                membership_id=linked_membership.id,
            ),
            headers=owner_headers,
        ),
        status_code=404,
        code="tenant_membership_not_found",
    )

    after_rejected = get_professional(
        client,
        tenant_id=tenant.id,
        professional_id=professional_id,
        headers=owner_headers,
    )
    assert after_rejected == before_rejected
    assert integrity_factory.membership_snapshot(linked_membership.id) is not None
    assert integrity_factory.membership_snapshot(tenant.owner_membership_id) is not None
    assert integrity_factory.membership_snapshot(admin_membership.id) is not None

    audits_after_rejected = professional_membership_audits(
        fetch_audit_items(
            client,
            tenant_id=tenant.id,
            headers=owner_headers,
            resource_type="professional",
            resource_id=professional_id,
        )
    )
    assert audits_after_rejected == audits_after_unlinked_removal


def test_database_rejects_duplicate_membership_link_and_preserves_valid_association(
    db_session: Session,
) -> None:
    tenant, membership = _create_constraint_tenant_and_membership(
        db_session,
        name="Constraint Unique Clinic",
    )
    valid = Professional(
        tenant_id=tenant.id,
        membership_id=membership.id,
        full_name="Drew Parker",
    )
    db_session.add(valid)
    db_session.flush()
    db_session.refresh(valid)
    valid_id = valid.id
    valid_membership_id = valid.membership_id
    valid_version = valid.version

    with pytest.raises(IntegrityError) as error, db_session.begin_nested():
        db_session.add(
            Professional(
                tenant_id=tenant.id,
                membership_id=membership.id,
                full_name="Quinn Baker",
            )
        )
        db_session.flush()

    assert _constraint_name(error.value) == "uq_professionals_membership_id"

    persisted = db_session.execute(
        select(Professional).where(Professional.id == valid_id)
    ).scalar_one()
    assert persisted.membership_id == valid_membership_id == membership.id
    assert persisted.version == valid_version
    assert (
        db_session.execute(
            select(Professional).where(Professional.membership_id == membership.id)
        ).scalar_one()
        is persisted
    )


def test_database_rejects_cross_tenant_membership_link_and_preserves_valid_association(
    db_session: Session,
) -> None:
    owning_tenant, owning_membership = _create_constraint_tenant_and_membership(
        db_session,
        name="Constraint Owning Clinic",
    )
    foreign_tenant, foreign_membership = _create_constraint_tenant_and_membership(
        db_session,
        name="Constraint Foreign Clinic",
    )
    valid = Professional(
        tenant_id=owning_tenant.id,
        membership_id=owning_membership.id,
        full_name="Skyler Lane",
    )
    db_session.add(valid)
    db_session.flush()
    db_session.refresh(valid)
    valid_id = valid.id
    valid_membership_id = valid.membership_id

    with pytest.raises(IntegrityError) as error, db_session.begin_nested():
        db_session.add(
            Professional(
                tenant_id=owning_tenant.id,
                membership_id=foreign_membership.id,
                full_name="Cross Tenant",
            )
        )
        db_session.flush()

    assert _constraint_name(error.value) == "fk_professionals_tenant_membership_memberships"

    persisted = db_session.execute(
        select(Professional).where(Professional.id == valid_id)
    ).scalar_one()
    assert persisted.membership_id == valid_membership_id == owning_membership.id
    assert foreign_tenant.id != owning_tenant.id
    assert (
        db_session.execute(
            select(Professional).where(Professional.membership_id == foreign_membership.id)
        ).scalar_one_or_none()
        is None
    )
