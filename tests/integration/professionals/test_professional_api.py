from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete
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
from clinicops.main import create_app
from clinicops.professionals.models import Professional
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)

PASSWORD = "Correct-Horse-Battery-Staple-2026!"
REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"

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

EXPECTED_MUTATION_ACTIONS = frozenset(
    {
        AuditAction.PROFESSIONAL_CREATED.value,
        AuditAction.PROFESSIONAL_UPDATED.value,
        AuditAction.PROFESSIONAL_ARCHIVED.value,
        AuditAction.PROFESSIONAL_RESTORED.value,
        AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value,
        AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value,
    }
)


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


class ProfessionalApiDataFactory:
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

    headers = {"Authorization": (f"Bearer {response.json()['access_token']}")}
    if request_id is not None:
        headers[REQUEST_ID_HEADER] = request_id
    if correlation_id is not None:
        headers[CORRELATION_ID_HEADER] = correlation_id

    return headers


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
        "external_reference": "PROVIDER-100",
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


def test_owner_professional_api_lifecycle_smoke(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="professional-owner",
    )
    link_user = professional_api_data_factory.create_user(
        email_prefix="professional-link-member",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Professional Smoke Clinic",
    )
    link_membership = professional_api_data_factory.add_membership(
        tenant=tenant,
        user=link_user,
        role=TenantRole.STAFF,
    )

    headers = login_headers(client, owner)
    collection_path = professionals_collection_path(tenant.id)

    create_response = create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert create_response.request.url.path == collection_path
    assert created["tenant_id"] == str(tenant.id)
    assert created["membership_id"] is None
    assert created["status"] == "active"
    assert created["version"] == 1
    professional_id = created["id"]
    item_path = professional_path(tenant.id, professional_id)

    list_response = client.get(collection_path, headers=headers)
    assert list_response.status_code == 200
    assert list_response.request.url.path == collection_path
    listed = list_response.json()
    assert "items" in listed
    assert "next_cursor" in listed
    assert [item["id"] for item in listed["items"]] == [professional_id]
    assert listed["next_cursor"] is None

    get_response = client.get(item_path, headers=headers)
    assert get_response.status_code == 200
    assert get_response.request.url.path == item_path
    assert get_response.json()["id"] == professional_id
    assert get_response.json()["version"] == 1
    assert get_response.json()["membership_id"] is None

    patch_response = client.patch(
        item_path,
        headers=headers,
        json={
            "expected_version": 1,
            "specialty": "Orthodontics",
            "email": "updated@example.com",
        },
    )
    assert patch_response.status_code == 200
    assert patch_response.request.url.path == item_path
    patched = patch_response.json()
    assert patched["specialty"] == "Orthodontics"
    assert patched["email"] == "updated@example.com"
    assert patched["membership_id"] is None
    assert patched["version"] == 2

    archive_path = PROFESSIONAL_ARCHIVE_PATH.format(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )
    archive_response = client.post(
        archive_path,
        headers=headers,
        json={"expected_version": 2},
    )
    assert archive_response.status_code == 200
    assert archive_response.request.url.path == archive_path
    archived = archive_response.json()
    assert archived["status"] == "archived"
    assert archived["version"] == 3
    assert archived["membership_id"] is None

    archived_get_response = client.get(item_path, headers=headers)
    assert archived_get_response.status_code == 200
    assert archived_get_response.json()["status"] == "archived"
    assert archived_get_response.json()["version"] == 3

    restore_path = PROFESSIONAL_RESTORE_PATH.format(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )
    restore_response = client.post(
        restore_path,
        headers=headers,
        json={"expected_version": 3},
    )
    assert restore_response.status_code == 200
    assert restore_response.request.url.path == restore_path
    restored = restore_response.json()
    assert restored["status"] == "active"
    assert restored["version"] == 4
    assert restored["membership_id"] is None
    profile_before_link = _profile_fields(restored)

    link_path = PROFESSIONAL_LINK_PATH.format(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )
    link_response = client.post(
        link_path,
        headers=headers,
        json={
            "membership_id": str(link_membership.id),
            "expected_version": 4,
        },
    )
    assert link_response.status_code == 200
    assert link_response.request.url.path == link_path
    linked = link_response.json()
    assert linked["membership_id"] == str(link_membership.id)
    assert linked["version"] == 5
    assert _profile_fields(linked) == profile_before_link

    unlink_path = PROFESSIONAL_UNLINK_PATH.format(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )
    unlink_response = client.post(
        unlink_path,
        headers=headers,
        json={"expected_version": 5},
    )
    assert unlink_response.status_code == 200
    assert unlink_response.request.url.path == unlink_path
    unlinked = unlink_response.json()
    assert unlinked["membership_id"] is None
    assert unlinked["version"] == 6
    assert _profile_fields(unlinked) == profile_before_link

    audit_response = client.get(
        f"/api/v1/tenants/{tenant.id}/audit-logs",
        headers=headers,
        params={
            "resource_type": "professional",
            "resource_id": professional_id,
        },
    )
    assert audit_response.status_code == 200
    audit_actions = {item["action"] for item in audit_response.json()["items"]}
    assert audit_actions == EXPECTED_MUTATION_ACTIONS


def test_staff_can_read_list_but_not_mutate_professionals(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="professional-staff-owner",
    )
    staff = professional_api_data_factory.create_user(
        email_prefix="professional-staff",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Professional Staff Clinic",
    )
    professional_api_data_factory.add_membership(
        tenant=tenant,
        user=staff,
        role=TenantRole.STAFF,
    )

    owner_headers = login_headers(client, owner)
    created = create_professional(
        client,
        tenant_id=tenant.id,
        headers=owner_headers,
    ).json()
    item_path = professional_path(tenant.id, created["id"])
    collection_path = professionals_collection_path(tenant.id)

    staff_headers = login_headers(client, staff)

    list_response = client.get(collection_path, headers=staff_headers)
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["items"]] == [created["id"]]

    get_response = client.get(item_path, headers=staff_headers)
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]

    patch_response = client.patch(
        item_path,
        headers=staff_headers,
        json={
            "expected_version": 1,
            "specialty": "Blocked Specialty",
        },
    )
    assert_problem(
        patch_response,
        status_code=403,
        code="tenant_permission_denied",
    )

    verification_response = client.get(item_path, headers=staff_headers)
    assert verification_response.status_code == 200
    assert verification_response.json()["specialty"] == "Dentistry"
    assert verification_response.json()["version"] == 1


def test_openapi_does_not_expose_professional_delete() -> None:
    paths = create_app().openapi()["paths"]

    collection_path = "/api/v1/tenants/{tenant_id}/professionals"
    item_path = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}"
    archive_path = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/archive"
    restore_path = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/restore"
    link_path = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/link-membership"
    unlink_path = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/unlink-membership"

    assert set(paths[collection_path]) == {"get", "post"}
    assert set(paths[item_path]) == {"get", "patch"}
    assert set(paths[archive_path]) == {"post"}
    assert set(paths[restore_path]) == {"post"}
    assert set(paths[link_path]) == {"post"}
    assert set(paths[unlink_path]) == {"post"}
    assert "delete" not in paths[item_path]
    assert "delete" not in paths[collection_path]
