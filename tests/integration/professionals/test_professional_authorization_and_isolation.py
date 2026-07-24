from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.api.errors import PROBLEM_MEDIA_TYPE
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

PROFESSIONAL_AUDIT_ACTIONS = frozenset(
    {
        AuditAction.PROFESSIONAL_CREATED.value,
        AuditAction.PROFESSIONAL_UPDATED.value,
        AuditAction.PROFESSIONAL_ARCHIVED.value,
        AuditAction.PROFESSIONAL_RESTORED.value,
        AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value,
        AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value,
    }
)

PRIVILEGED_ROLES = (
    TenantRole.OWNER,
    TenantRole.ADMIN,
)


class ProfessionalOperation(StrEnum):
    LIST = "list"
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    ARCHIVE = "archive"
    RESTORE = "restore"
    LINK = "link"
    UNLINK = "unlink"


@dataclass(frozen=True, slots=True)
class CommittedUser:
    id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class CommittedTenant:
    id: UUID


@dataclass(frozen=True, slots=True)
class CommittedMembership:
    id: UUID


@dataclass(frozen=True, slots=True)
class ProfessionalSnapshot:
    id: str
    tenant_id: str
    full_name: str
    email: str | None
    registration_number: str | None
    specialty: str | None
    status: str
    version: int
    membership_id: str | None


class ProfessionalApiDataFactory:
    """Committed multi-tenant state for Professional HTTP authorization tests."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._user_ids: set[UUID] = set()
        self._tenant_ids: set[UUID] = set()
        self._password_hash = Argon2PasswordHasher().hash(PASSWORD)

    def create_user(
        self,
        *,
        email_prefix: str,
    ) -> CommittedUser:
        user = User(
            email=(f"{email_prefix}-{uuid4().hex}@example.com"),
            status=UserStatus.ACTIVE,
            password_credential=PasswordCredential(
                password_hash=self._password_hash,
            ),
        )
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)

        self._user_ids.add(user.id)
        return CommittedUser(
            id=user.id,
            email=user.email,
        )

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
        self._session.add(tenant)
        self._session.add(owner_membership)
        self._session.commit()
        self._session.refresh(tenant)

        self._tenant_ids.add(tenant.id)
        return CommittedTenant(id=tenant.id)

    def add_membership(
        self,
        *,
        tenant: CommittedTenant,
        user: CommittedUser,
        role: TenantRole,
    ) -> CommittedMembership:
        membership = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add(membership)
        self._session.commit()
        self._session.refresh(membership)

        return CommittedMembership(id=membership.id)

    def count_professional_audit_entries(
        self,
        *,
        tenant_id: UUID,
    ) -> int:
        self._session.expire_all()
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(AuditLogEntry)
                .where(AuditLogEntry.tenant_id == tenant_id)
                .where(AuditLogEntry.resource_type == "professional")
                .where(AuditLogEntry.action.in_(PROFESSIONAL_AUDIT_ACTIONS))
            )
            or 0
        )

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
def professional_api_data_factory() -> Iterator[ProfessionalApiDataFactory]:
    session = Session(get_engine())
    factory = ProfessionalApiDataFactory(session)

    try:
        yield factory
    finally:
        factory.cleanup()
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


def professionals_collection_path(tenant_id: UUID) -> str:
    return PROFESSIONAL_COLLECTION_PATH.format(tenant_id=tenant_id)


def professional_path(
    tenant_id: UUID,
    professional_id: str,
) -> str:
    return PROFESSIONAL_ITEM_PATH.format(
        tenant_id=tenant_id,
        professional_id=professional_id,
    )


def create_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
    payload: dict[str, object] | None = None,
) -> Response:
    body: dict[str, object] = {
        "full_name": "Morgan Reed",
        "specialty": "Dentistry",
        "registration_number": "DDS-48291",
        "registration_region": "CA",
        "email": "morgan@example.com",
        "phone": "+1-202-555-0130",
        "external_reference": f"PROVIDER-{uuid4().hex[:8]}",
    }
    if payload is not None:
        body.update(payload)

    return client.post(
        professionals_collection_path(tenant_id),
        headers=headers,
        json=body,
    )


def assert_problem(
    response: Response,
    *,
    status_code: int,
    code: str,
) -> dict[str, object]:
    body = cast(dict[str, object], response.json())

    assert response.status_code == status_code
    assert response.headers["content-type"] == (PROBLEM_MEDIA_TYPE)
    assert body["status"] == status_code
    assert body["code"] == code
    assert body["type"] == (f"urn:clinicops:problem:{code}")
    assert body["request_id"]
    assert body["correlation_id"]

    return body


def snapshot_from_body(body: dict[str, object]) -> ProfessionalSnapshot:
    return ProfessionalSnapshot(
        id=str(body["id"]),
        tenant_id=str(body["tenant_id"]),
        full_name=str(body["full_name"]),
        email=cast(str | None, body["email"]),
        registration_number=cast(str | None, body["registration_number"]),
        specialty=cast(str | None, body["specialty"]),
        status=str(body["status"]),
        version=int(cast(int, body["version"])),
        membership_id=(None if body["membership_id"] is None else str(body["membership_id"])),
    )


def fetch_professional_snapshot(
    client: TestClient,
    *,
    tenant_id: UUID,
    professional_id: str,
    headers: dict[str, str],
) -> ProfessionalSnapshot:
    response = client.get(
        professional_path(tenant_id, professional_id),
        headers=headers,
    )
    assert response.status_code == 200
    return snapshot_from_body(response.json())


def actor_for_role(
    factory: ProfessionalApiDataFactory,
    *,
    owner: CommittedUser,
    tenant: CommittedTenant,
    role: TenantRole,
    email_prefix: str,
) -> CommittedUser:
    if role is TenantRole.OWNER:
        return owner

    actor = factory.create_user(email_prefix=email_prefix)
    factory.add_membership(
        tenant=tenant,
        user=actor,
        role=role,
    )
    return actor


def prepare_active_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    owner_headers: dict[str, str],
    payload: dict[str, object] | None = None,
) -> ProfessionalSnapshot:
    response = create_professional(
        client,
        tenant_id=tenant_id,
        headers=owner_headers,
        payload=payload,
    )
    assert response.status_code == 201
    return snapshot_from_body(response.json())


def prepare_archived_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    owner_headers: dict[str, str],
) -> ProfessionalSnapshot:
    created = prepare_active_professional(
        client,
        tenant_id=tenant_id,
        owner_headers=owner_headers,
    )
    archive_response = client.post(
        PROFESSIONAL_ARCHIVE_PATH.format(
            tenant_id=tenant_id,
            professional_id=created.id,
        ),
        headers=owner_headers,
        json={"expected_version": created.version},
    )
    assert archive_response.status_code == 200
    return snapshot_from_body(archive_response.json())


def prepare_linked_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    owner_headers: dict[str, str],
    membership_id: UUID,
) -> ProfessionalSnapshot:
    created = prepare_active_professional(
        client,
        tenant_id=tenant_id,
        owner_headers=owner_headers,
    )
    link_response = client.post(
        PROFESSIONAL_LINK_PATH.format(
            tenant_id=tenant_id,
            professional_id=created.id,
        ),
        headers=owner_headers,
        json={
            "membership_id": str(membership_id),
            "expected_version": created.version,
        },
    )
    assert link_response.status_code == 200
    return snapshot_from_body(link_response.json())


def invoke_operation(
    client: TestClient,
    *,
    operation: ProfessionalOperation,
    tenant_id: UUID,
    headers: dict[str, str],
    professional: ProfessionalSnapshot | None = None,
    link_membership_id: UUID | None = None,
) -> Response:
    collection_path = professionals_collection_path(tenant_id)

    if operation is ProfessionalOperation.LIST:
        return client.get(collection_path, headers=headers)

    if operation is ProfessionalOperation.CREATE:
        return create_professional(
            client,
            tenant_id=tenant_id,
            headers=headers,
            payload={
                "full_name": "Casey Quinn",
                "email": "casey@example.com",
                "registration_number": "DDS-99001",
                "external_reference": f"CREATE-{uuid4().hex[:8]}",
            },
        )

    assert professional is not None
    item_path = professional_path(tenant_id, professional.id)

    if operation is ProfessionalOperation.READ:
        return client.get(item_path, headers=headers)

    if operation is ProfessionalOperation.UPDATE:
        return client.patch(
            item_path,
            headers=headers,
            json={
                "expected_version": professional.version,
                "specialty": "Orthodontics",
            },
        )

    if operation is ProfessionalOperation.ARCHIVE:
        return client.post(
            PROFESSIONAL_ARCHIVE_PATH.format(
                tenant_id=tenant_id,
                professional_id=professional.id,
            ),
            headers=headers,
            json={"expected_version": professional.version},
        )

    if operation is ProfessionalOperation.RESTORE:
        return client.post(
            PROFESSIONAL_RESTORE_PATH.format(
                tenant_id=tenant_id,
                professional_id=professional.id,
            ),
            headers=headers,
            json={"expected_version": professional.version},
        )

    if operation is ProfessionalOperation.LINK:
        assert link_membership_id is not None
        return client.post(
            PROFESSIONAL_LINK_PATH.format(
                tenant_id=tenant_id,
                professional_id=professional.id,
            ),
            headers=headers,
            json={
                "membership_id": str(link_membership_id),
                "expected_version": professional.version,
            },
        )

    if operation is ProfessionalOperation.UNLINK:
        return client.post(
            PROFESSIONAL_UNLINK_PATH.format(
                tenant_id=tenant_id,
                professional_id=professional.id,
            ),
            headers=headers,
            json={"expected_version": professional.version},
        )

    raise AssertionError(f"Unhandled operation: {operation}")


def prepare_operation_target(
    client: TestClient,
    *,
    operation: ProfessionalOperation,
    tenant_id: UUID,
    owner_headers: dict[str, str],
    link_membership_id: UUID,
) -> ProfessionalSnapshot | None:
    if operation is ProfessionalOperation.CREATE:
        return None

    if operation is ProfessionalOperation.LIST:
        return prepare_active_professional(
            client,
            tenant_id=tenant_id,
            owner_headers=owner_headers,
        )

    if operation is ProfessionalOperation.RESTORE:
        return prepare_archived_professional(
            client,
            tenant_id=tenant_id,
            owner_headers=owner_headers,
        )

    if operation is ProfessionalOperation.UNLINK:
        return prepare_linked_professional(
            client,
            tenant_id=tenant_id,
            owner_headers=owner_headers,
            membership_id=link_membership_id,
        )

    return prepare_active_professional(
        client,
        tenant_id=tenant_id,
        owner_headers=owner_headers,
    )


@pytest.mark.parametrize("role", PRIVILEGED_ROLES)
@pytest.mark.parametrize(
    "operation",
    list(ProfessionalOperation),
)
def test_owner_and_admin_succeed_for_every_professional_operation(
    role: TenantRole,
    operation: ProfessionalOperation,
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix=f"prof-auth-{role.value}-owner",
    )
    link_user = professional_api_data_factory.create_user(
        email_prefix=f"prof-auth-{role.value}-link",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name=f"{role.value.title()} Professional Auth Clinic",
    )
    link_membership = professional_api_data_factory.add_membership(
        tenant=tenant,
        user=link_user,
        role=TenantRole.STAFF,
    )
    actor = actor_for_role(
        professional_api_data_factory,
        owner=owner,
        tenant=tenant,
        role=role,
        email_prefix=f"prof-auth-{role.value}-actor",
    )

    owner_headers = login_headers(client, owner)
    actor_headers = login_headers(client, actor)
    professional = prepare_operation_target(
        client,
        operation=operation,
        tenant_id=tenant.id,
        owner_headers=owner_headers,
        link_membership_id=link_membership.id,
    )

    response = invoke_operation(
        client,
        operation=operation,
        tenant_id=tenant.id,
        headers=actor_headers,
        professional=professional,
        link_membership_id=link_membership.id,
    )

    if operation is ProfessionalOperation.CREATE:
        assert response.status_code == 201
        assert response.json()["tenant_id"] == str(tenant.id)
        assert response.json()["status"] == "active"
        return

    assert response.status_code == 200

    if operation is ProfessionalOperation.LIST:
        assert professional is not None
        assert [item["id"] for item in response.json()["items"]] == [professional.id]
        return

    assert professional is not None
    body = response.json()
    assert body["id"] == professional.id
    assert body["tenant_id"] == str(tenant.id)

    if operation is ProfessionalOperation.UPDATE:
        assert body["specialty"] == "Orthodontics"
        assert body["version"] == professional.version + 1
    elif operation is ProfessionalOperation.ARCHIVE:
        assert body["status"] == "archived"
        assert body["version"] == professional.version + 1
    elif operation is ProfessionalOperation.RESTORE:
        assert body["status"] == "active"
        assert body["version"] == professional.version + 1
    elif operation is ProfessionalOperation.LINK:
        assert body["membership_id"] == str(link_membership.id)
        assert body["version"] == professional.version + 1
    elif operation is ProfessionalOperation.UNLINK:
        assert body["membership_id"] is None
        assert body["version"] == professional.version + 1


@pytest.mark.parametrize(
    ("operation", "expected_status"),
    [
        (ProfessionalOperation.LIST, 200),
        (ProfessionalOperation.READ, 200),
        (ProfessionalOperation.CREATE, 403),
        (ProfessionalOperation.UPDATE, 403),
        (ProfessionalOperation.ARCHIVE, 403),
        (ProfessionalOperation.RESTORE, 403),
        (ProfessionalOperation.LINK, 403),
        (ProfessionalOperation.UNLINK, 403),
    ],
)
def test_staff_professional_authorization_matrix(
    operation: ProfessionalOperation,
    expected_status: int,
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="prof-staff-matrix-owner",
    )
    staff = professional_api_data_factory.create_user(
        email_prefix="prof-staff-matrix-actor",
    )
    link_user = professional_api_data_factory.create_user(
        email_prefix="prof-staff-matrix-link",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Staff Professional Matrix Clinic",
    )
    professional_api_data_factory.add_membership(
        tenant=tenant,
        user=staff,
        role=TenantRole.STAFF,
    )
    link_membership = professional_api_data_factory.add_membership(
        tenant=tenant,
        user=link_user,
        role=TenantRole.STAFF,
    )

    owner_headers = login_headers(client, owner)
    staff_headers = login_headers(client, staff)
    professional = prepare_operation_target(
        client,
        operation=operation,
        tenant_id=tenant.id,
        owner_headers=owner_headers,
        link_membership_id=link_membership.id,
    )
    audit_before = professional_api_data_factory.count_professional_audit_entries(
        tenant_id=tenant.id,
    )
    before_snapshot = (
        None
        if professional is None
        else fetch_professional_snapshot(
            client,
            tenant_id=tenant.id,
            professional_id=professional.id,
            headers=owner_headers,
        )
    )

    response = invoke_operation(
        client,
        operation=operation,
        tenant_id=tenant.id,
        headers=staff_headers,
        professional=professional,
        link_membership_id=link_membership.id,
    )

    if expected_status == 200:
        assert response.status_code == 200
        assert professional is not None
        if operation is ProfessionalOperation.LIST:
            assert [item["id"] for item in response.json()["items"]] == [professional.id]
        else:
            assert response.json()["id"] == professional.id
    else:
        assert_problem(
            response,
            status_code=403,
            code="tenant_permission_denied",
        )
        if before_snapshot is not None:
            after_snapshot = fetch_professional_snapshot(
                client,
                tenant_id=tenant.id,
                professional_id=before_snapshot.id,
                headers=owner_headers,
            )
            assert after_snapshot == before_snapshot

    audit_after = professional_api_data_factory.count_professional_audit_entries(
        tenant_id=tenant.id,
    )
    assert audit_after == audit_before


def test_unauthenticated_requests_are_rejected(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="prof-unauth-owner",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Unauth Professional Clinic",
    )
    audit_before = professional_api_data_factory.count_professional_audit_entries(
        tenant_id=tenant.id,
    )

    list_response = client.get(professionals_collection_path(tenant.id))
    create_response = create_professional(
        client,
        tenant_id=tenant.id,
        headers={},
    )

    assert_problem(
        list_response,
        status_code=401,
        code="unauthorized",
    )
    assert_problem(
        create_response,
        status_code=401,
        code="unauthorized",
    )
    assert list_response.headers["www-authenticate"] == "Bearer"
    assert create_response.headers["www-authenticate"] == "Bearer"
    assert (
        professional_api_data_factory.count_professional_audit_entries(
            tenant_id=tenant.id,
        )
        == audit_before
    )


def test_foreign_tenant_member_cannot_target_another_tenant(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner_a = professional_api_data_factory.create_user(
        email_prefix="prof-iso-owner-a",
    )
    owner_b = professional_api_data_factory.create_user(
        email_prefix="prof-iso-owner-b",
    )
    tenant_a = professional_api_data_factory.create_tenant(
        owner=owner_a,
        name="Isolation Tenant A",
    )
    professional_api_data_factory.create_tenant(
        owner=owner_b,
        name="Isolation Tenant B",
    )

    headers_b = login_headers(client, owner_b)
    audit_before = professional_api_data_factory.count_professional_audit_entries(
        tenant_id=tenant_a.id,
    )

    list_response = client.get(
        professionals_collection_path(tenant_a.id),
        headers=headers_b,
    )
    create_response = create_professional(
        client,
        tenant_id=tenant_a.id,
        headers=headers_b,
    )

    assert_problem(
        list_response,
        status_code=404,
        code="tenant_membership_not_found",
    )
    assert_problem(
        create_response,
        status_code=404,
        code="tenant_membership_not_found",
    )
    assert (
        professional_api_data_factory.count_professional_audit_entries(
            tenant_id=tenant_a.id,
        )
        == audit_before
    )


@pytest.mark.parametrize("role", PRIVILEGED_ROLES)
@pytest.mark.parametrize(
    "operation",
    [
        ProfessionalOperation.READ,
        ProfessionalOperation.UPDATE,
        ProfessionalOperation.ARCHIVE,
        ProfessionalOperation.RESTORE,
        ProfessionalOperation.LINK,
        ProfessionalOperation.UNLINK,
    ],
)
def test_cross_tenant_professional_item_operations_are_not_found(
    role: TenantRole,
    operation: ProfessionalOperation,
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner_a = professional_api_data_factory.create_user(
        email_prefix=f"prof-x-{role.value}-owner-a",
    )
    owner_b = professional_api_data_factory.create_user(
        email_prefix=f"prof-x-{role.value}-owner-b",
    )
    link_user_a = professional_api_data_factory.create_user(
        email_prefix=f"prof-x-{role.value}-link-a",
    )
    link_user_b = professional_api_data_factory.create_user(
        email_prefix=f"prof-x-{role.value}-link-b",
    )
    tenant_a = professional_api_data_factory.create_tenant(
        owner=owner_a,
        name=f"Cross Tenant A {role.value}",
    )
    tenant_b = professional_api_data_factory.create_tenant(
        owner=owner_b,
        name=f"Cross Tenant B {role.value}",
    )
    link_membership_a = professional_api_data_factory.add_membership(
        tenant=tenant_a,
        user=link_user_a,
        role=TenantRole.STAFF,
    )
    link_membership_b = professional_api_data_factory.add_membership(
        tenant=tenant_b,
        user=link_user_b,
        role=TenantRole.STAFF,
    )
    actor_a = actor_for_role(
        professional_api_data_factory,
        owner=owner_a,
        tenant=tenant_a,
        role=role,
        email_prefix=f"prof-x-{role.value}-actor-a",
    )

    owner_b_headers = login_headers(client, owner_b)
    actor_a_headers = login_headers(client, actor_a)

    foreign = prepare_operation_target(
        client,
        operation=operation,
        tenant_id=tenant_b.id,
        owner_headers=owner_b_headers,
        link_membership_id=link_membership_b.id,
    )
    assert foreign is not None
    before = fetch_professional_snapshot(
        client,
        tenant_id=tenant_b.id,
        professional_id=foreign.id,
        headers=owner_b_headers,
    )
    audit_before_a = professional_api_data_factory.count_professional_audit_entries(
        tenant_id=tenant_a.id,
    )
    audit_before_b = professional_api_data_factory.count_professional_audit_entries(
        tenant_id=tenant_b.id,
    )

    missing_id = str(uuid4())
    missing_response = client.get(
        professional_path(tenant_a.id, missing_id),
        headers=actor_a_headers,
    )
    missing_problem = assert_problem(
        missing_response,
        status_code=404,
        code="professional_not_found",
    )

    response = invoke_operation(
        client,
        operation=operation,
        tenant_id=tenant_a.id,
        headers=actor_a_headers,
        professional=foreign,
        link_membership_id=link_membership_a.id,
    )
    problem = assert_problem(
        response,
        status_code=404,
        code="professional_not_found",
    )

    assert problem["title"] == missing_problem["title"]
    assert problem["detail"] == missing_problem["detail"]
    assert problem["type"] == missing_problem["type"]
    assert problem["code"] == missing_problem["code"]

    response_text = response.text
    assert before.email is not None
    assert before.registration_number is not None
    assert before.tenant_id not in response_text
    assert before.full_name not in response_text
    assert before.email not in response_text
    assert before.registration_number not in response_text
    assert before.id not in response_text

    after = fetch_professional_snapshot(
        client,
        tenant_id=tenant_b.id,
        professional_id=foreign.id,
        headers=owner_b_headers,
    )
    assert after == before
    assert (
        professional_api_data_factory.count_professional_audit_entries(
            tenant_id=tenant_a.id,
        )
        == audit_before_a
    )
    assert (
        professional_api_data_factory.count_professional_audit_entries(
            tenant_id=tenant_b.id,
        )
        == audit_before_b
    )


def test_authorization_failures_create_no_professional_audit_entries(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="prof-audit-fail-owner",
    )
    staff = professional_api_data_factory.create_user(
        email_prefix="prof-audit-fail-staff",
    )
    link_user = professional_api_data_factory.create_user(
        email_prefix="prof-audit-fail-link",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Audit Failure Professional Clinic",
    )
    professional_api_data_factory.add_membership(
        tenant=tenant,
        user=staff,
        role=TenantRole.STAFF,
    )
    link_membership = professional_api_data_factory.add_membership(
        tenant=tenant,
        user=link_user,
        role=TenantRole.STAFF,
    )

    owner_headers = login_headers(client, owner)
    staff_headers = login_headers(client, staff)
    active = prepare_active_professional(
        client,
        tenant_id=tenant.id,
        owner_headers=owner_headers,
    )
    archived = prepare_archived_professional(
        client,
        tenant_id=tenant.id,
        owner_headers=owner_headers,
    )
    linked = prepare_linked_professional(
        client,
        tenant_id=tenant.id,
        owner_headers=owner_headers,
        membership_id=link_membership.id,
    )
    audit_before = professional_api_data_factory.count_professional_audit_entries(
        tenant_id=tenant.id,
    )

    denied_responses = [
        create_professional(
            client,
            tenant_id=tenant.id,
            headers=staff_headers,
        ),
        client.patch(
            professional_path(tenant.id, active.id),
            headers=staff_headers,
            json={
                "expected_version": active.version,
                "specialty": "Blocked",
            },
        ),
        client.post(
            PROFESSIONAL_ARCHIVE_PATH.format(
                tenant_id=tenant.id,
                professional_id=active.id,
            ),
            headers=staff_headers,
            json={"expected_version": active.version},
        ),
        client.post(
            PROFESSIONAL_RESTORE_PATH.format(
                tenant_id=tenant.id,
                professional_id=archived.id,
            ),
            headers=staff_headers,
            json={"expected_version": archived.version},
        ),
        client.post(
            PROFESSIONAL_LINK_PATH.format(
                tenant_id=tenant.id,
                professional_id=active.id,
            ),
            headers=staff_headers,
            json={
                "membership_id": str(link_membership.id),
                "expected_version": active.version,
            },
        ),
        client.post(
            PROFESSIONAL_UNLINK_PATH.format(
                tenant_id=tenant.id,
                professional_id=linked.id,
            ),
            headers=staff_headers,
            json={"expected_version": linked.version},
        ),
    ]

    for response in denied_responses:
        assert_problem(
            response,
            status_code=403,
            code="tenant_permission_denied",
        )

    assert (
        professional_api_data_factory.count_professional_audit_entries(
            tenant_id=tenant.id,
        )
        == audit_before
    )


def test_read_operations_create_no_professional_audit_entries(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="prof-audit-read-owner",
    )
    staff = professional_api_data_factory.create_user(
        email_prefix="prof-audit-read-staff",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Audit Read Professional Clinic",
    )
    professional_api_data_factory.add_membership(
        tenant=tenant,
        user=staff,
        role=TenantRole.STAFF,
    )

    owner_headers = login_headers(client, owner)
    staff_headers = login_headers(client, staff)
    created = prepare_active_professional(
        client,
        tenant_id=tenant.id,
        owner_headers=owner_headers,
        payload={
            "full_name": "Taylor Brooks",
            "email": "taylor@example.com",
            "registration_number": "DDS-70001",
        },
    )
    audit_before = professional_api_data_factory.count_professional_audit_entries(
        tenant_id=tenant.id,
    )

    for headers in (owner_headers, staff_headers):
        list_response = client.get(
            professionals_collection_path(tenant.id),
            headers=headers,
        )
        get_response = client.get(
            professional_path(tenant.id, created.id),
            headers=headers,
        )
        assert list_response.status_code == 200
        assert get_response.status_code == 200
        assert get_response.json()["id"] == created.id

    assert (
        professional_api_data_factory.count_professional_audit_entries(
            tenant_id=tenant.id,
        )
        == audit_before
    )
