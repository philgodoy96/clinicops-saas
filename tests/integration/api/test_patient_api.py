from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.api.errors import PROBLEM_MEDIA_TYPE
from clinicops.audit.models import AuditLogEntry
from clinicops.authentication.models import AuthSession
from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import Argon2PasswordHasher
from clinicops.patients.models import Patient
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


@dataclass(frozen=True, slots=True)
class CommittedUser:
    id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class CommittedTenant:
    id: UUID


class PatientApiDataFactory:
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
    ) -> None:
        membership = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add(membership)
        self._session.commit()

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
                delete(Patient)
                .where(Patient.tenant_id.in_(self._tenant_ids))
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
def patient_api_data_factory() -> Iterator[PatientApiDataFactory]:
    session = Session(get_engine())
    factory = PatientApiDataFactory(session)

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


def create_patient(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
    payload: dict[str, object] | None = None,
) -> Response:
    body: dict[str, object] = {
        "full_name": "Jordan Lee",
    }
    if payload is not None:
        body.update(payload)

    return client.post(
        f"/api/v1/tenants/{tenant_id}/patients",
        headers=headers,
        json=body,
    )


def patient_path(
    tenant_id: UUID,
    patient_id: str,
) -> str:
    return f"/api/v1/tenants/{tenant_id}/patients/{patient_id}"


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


def test_staff_can_create_get_list_and_update_patient(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="patient-owner")
    staff = patient_api_data_factory.create_user(email_prefix="patient-staff")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Staff Patient Clinic",
    )
    patient_api_data_factory.add_membership(
        tenant=tenant,
        user=staff,
        role=TenantRole.STAFF,
    )

    request_id = str(uuid4())
    correlation_id = str(uuid4())
    headers = login_headers(
        client,
        staff,
        request_id=request_id,
        correlation_id=correlation_id,
    )

    create_response = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "email": "Jordan@Example.com",
            "phone": "+1-202-555-0184",
        },
    )

    assert create_response.status_code == 201
    assert create_response.headers[REQUEST_ID_HEADER] == (request_id)
    assert create_response.headers[CORRELATION_ID_HEADER] == correlation_id

    created = create_response.json()
    assert created["tenant_id"] == str(tenant.id)
    assert created["full_name"] == "Jordan Lee"
    assert created["email"] == "jordan@example.com"
    assert created["status"] == "active"
    assert created["version"] == 1

    get_response = client.get(
        patient_path(tenant.id, created["id"]),
        headers=headers,
    )
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]

    list_response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
    )
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["items"]] == [created["id"]]

    update_response = client.patch(
        patient_path(tenant.id, created["id"]),
        headers=headers,
        json={
            "expected_version": 1,
            "email": "updated@example.com",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["email"] == ("updated@example.com")
    assert update_response.json()["version"] == 2


def test_staff_cannot_archive_or_restore_patient(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="lifecycle-owner")
    staff = patient_api_data_factory.create_user(email_prefix="lifecycle-staff")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Staff Lifecycle Clinic",
    )
    patient_api_data_factory.add_membership(
        tenant=tenant,
        user=staff,
        role=TenantRole.STAFF,
    )
    headers = login_headers(client, staff)

    created = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
    ).json()
    path = patient_path(tenant.id, created["id"])

    archive_response = client.post(
        f"{path}/archive",
        headers=headers,
        json={"expected_version": 1},
    )
    assert_problem(
        archive_response,
        status_code=403,
        code="tenant_permission_denied",
    )

    restore_response = client.post(
        f"{path}/restore",
        headers=headers,
        json={"expected_version": 1},
    )
    assert_problem(
        restore_response,
        status_code=403,
        code="tenant_permission_denied",
    )

    get_response = client.get(path, headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "active"
    assert get_response.json()["version"] == 1


@pytest.mark.parametrize(
    "role",
    [
        TenantRole.OWNER,
        TenantRole.ADMIN,
    ],
)
def test_owner_and_admin_can_archive_and_restore_patient(
    role: TenantRole,
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix=f"{role.value}-owner")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name=f"{role.value.title()} Lifecycle Clinic",
    )

    actor = owner
    if role is TenantRole.ADMIN:
        actor = patient_api_data_factory.create_user(email_prefix="patient-admin")
        patient_api_data_factory.add_membership(
            tenant=tenant,
            user=actor,
            role=TenantRole.ADMIN,
        )

    headers = login_headers(client, actor)
    created_response = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
    )
    assert created_response.status_code == 201
    created = created_response.json()
    path = patient_path(tenant.id, created["id"])

    archive_response = client.post(
        f"{path}/archive",
        headers=headers,
        json={"expected_version": 1},
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"
    assert archive_response.json()["version"] == 2

    restore_response = client.post(
        f"{path}/restore",
        headers=headers,
        json={"expected_version": 2},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["status"] == "active"
    assert restore_response.json()["version"] == 3


def test_patient_resource_is_isolated_between_tenants(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="shared-owner")
    first_tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="First Isolation Clinic",
    )
    second_tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Second Isolation Clinic",
    )
    headers = login_headers(client, owner)

    foreign_create_response = create_patient(
        client,
        tenant_id=second_tenant.id,
        headers=headers,
        payload={"email": "foreign@example.com"},
    )
    assert foreign_create_response.status_code == 201
    foreign_patient = foreign_create_response.json()

    foreign_id = foreign_patient["id"]
    wrong_path = patient_path(
        first_tenant.id,
        foreign_id,
    )

    get_response = client.get(
        wrong_path,
        headers=headers,
    )
    assert_problem(
        get_response,
        status_code=404,
        code="patient_not_found",
    )
    assert foreign_id not in get_response.text

    update_response = client.patch(
        wrong_path,
        headers=headers,
        json={
            "expected_version": 1,
            "email": "leaked@example.com",
        },
    )
    assert_problem(
        update_response,
        status_code=404,
        code="patient_not_found",
    )

    archive_response = client.post(
        f"{wrong_path}/archive",
        headers=headers,
        json={"expected_version": 1},
    )
    assert_problem(
        archive_response,
        status_code=404,
        code="patient_not_found",
    )

    restore_response = client.post(
        f"{wrong_path}/restore",
        headers=headers,
        json={"expected_version": 1},
    )
    assert_problem(
        restore_response,
        status_code=404,
        code="patient_not_found",
    )

    list_response = client.get(
        f"/api/v1/tenants/{first_tenant.id}/patients",
        headers=headers,
    )
    assert list_response.status_code == 200
    assert list_response.json()["items"] == []
    assert foreign_id not in list_response.text

    verification_response = client.get(
        patient_path(second_tenant.id, foreign_id),
        headers=headers,
    )
    assert verification_response.status_code == 200
    assert verification_response.json()["email"] == ("foreign@example.com")
    assert verification_response.json()["status"] == "active"
    assert verification_response.json()["version"] == 1


def test_list_status_filters_and_default_active_scope(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="status-owner")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Status Filter Clinic",
    )
    headers = login_headers(client, owner)

    archived_patient = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={"full_name": "Archived Patient"},
    ).json()
    active_patient = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={"full_name": "Active Patient"},
    ).json()

    archive_response = client.post(
        (f"{patient_path(tenant.id, archived_patient['id'])}/archive"),
        headers=headers,
        json={"expected_version": 1},
    )
    assert archive_response.status_code == 200

    default_response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
    )
    archived_response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
        params={"status": "archived"},
    )
    all_response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
        params={"status": "all"},
    )

    assert {item["id"] for item in default_response.json()["items"]} == {active_patient["id"]}
    assert {item["id"] for item in archived_response.json()["items"]} == {archived_patient["id"]}
    assert {item["id"] for item in all_response.json()["items"]} == {
        active_patient["id"],
        archived_patient["id"],
    }


def test_cursor_pagination_is_stable_and_opaque(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="pagination-owner")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Pagination Clinic",
    )
    headers = login_headers(client, owner)

    created_ids = {
        create_patient(
            client,
            tenant_id=tenant.id,
            headers=headers,
            payload={"full_name": f"Patient {index}"},
        ).json()["id"]
        for index in range(5)
    }

    first_response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
        params={"limit": 2},
    )
    assert first_response.status_code == 200
    first_page = first_response.json()
    assert len(first_page["items"]) == 2
    assert isinstance(first_page["next_cursor"], str)
    assert first_page["next_cursor"]
    assert "created_at" not in first_page["next_cursor"]
    assert "patient_id" not in first_page["next_cursor"]

    second_response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
        params={
            "limit": 2,
            "cursor": first_page["next_cursor"],
        },
    )
    assert second_response.status_code == 200
    second_page = second_response.json()
    assert len(second_page["items"]) == 2
    assert second_page["next_cursor"]

    third_response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
        params={
            "limit": 2,
            "cursor": second_page["next_cursor"],
        },
    )
    assert third_response.status_code == 200
    third_page = third_response.json()
    assert len(third_page["items"]) == 1
    assert third_page["next_cursor"] is None

    paged_ids = [
        item["id"] for page in (first_page, second_page, third_page) for item in page["items"]
    ]
    assert len(paged_ids) == len(set(paged_ids))
    assert set(paged_ids) == created_ids


@pytest.mark.parametrize(
    ("search", "expected_name"),
    [
        ("Morgan", "Morgan Fields"),
        ("lookup@example.com", "Email Search"),
        ("555-0199", "Phone Search"),
        ("REF-SEARCH-100", "Reference Search"),
        ("100%", "Coverage 100% Complete"),
        ("A_B", "Code A_B"),
    ],
)
def test_search_uses_approved_fields_and_literal_wildcards(
    search: str,
    expected_name: str,
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix=f"search-{uuid4().hex[:8]}")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Search Clinic",
    )
    headers = login_headers(client, owner)

    payloads: list[dict[str, object]] = [
        {"full_name": "Morgan Fields"},
        {
            "full_name": "Email Search",
            "email": "lookup@example.com",
        },
        {
            "full_name": "Phone Search",
            "phone": "+1-202-555-0199",
        },
        {
            "full_name": "Reference Search",
            "external_reference": "REF-SEARCH-100",
        },
        {"full_name": "Coverage 100% Complete"},
        {"full_name": "Coverage 100X Complete"},
        {"full_name": "Code A_B"},
        {"full_name": "Code AXB"},
    ]
    for payload in payloads:
        response = create_patient(
            client,
            tenant_id=tenant.id,
            headers=headers,
            payload=payload,
        )
        assert response.status_code == 201

    response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
        params={"search": search},
    )

    assert response.status_code == 200
    names = [item["full_name"] for item in response.json()["items"]]
    assert names == [expected_name]


def test_patch_distinguishes_omitted_field_from_explicit_null(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="patch-owner")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Patch Semantics Clinic",
    )
    headers = login_headers(client, owner)

    created = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "email": "retained@example.com",
            "phone": "+1-202-555-0100",
        },
    ).json()
    path = patient_path(tenant.id, created["id"])

    omitted_email_response = client.patch(
        path,
        headers=headers,
        json={
            "expected_version": 1,
            "phone": "+1-202-555-0101",
        },
    )
    assert omitted_email_response.status_code == 200
    assert omitted_email_response.json()["email"] == ("retained@example.com")
    assert omitted_email_response.json()["phone"] == ("+1-202-555-0101")
    assert omitted_email_response.json()["version"] == 2

    explicit_null_response = client.patch(
        path,
        headers=headers,
        json={
            "expected_version": 2,
            "email": None,
        },
    )
    assert explicit_null_response.status_code == 200
    assert explicit_null_response.json()["email"] is None
    assert explicit_null_response.json()["phone"] == ("+1-202-555-0101")
    assert explicit_null_response.json()["version"] == 3


def test_expected_version_validation_and_stale_conflicts(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="version-owner")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Version Conflict Clinic",
    )
    headers = login_headers(client, owner)

    created = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
    ).json()
    path = patient_path(tenant.id, created["id"])

    missing_version_response = client.patch(
        path,
        headers=headers,
        json={"email": "missing@example.com"},
    )
    assert_problem(
        missing_version_response,
        status_code=422,
        code="request_validation_error",
    )

    invalid_version_response = client.patch(
        path,
        headers=headers,
        json={
            "expected_version": 0,
            "email": "invalid@example.com",
        },
    )
    assert_problem(
        invalid_version_response,
        status_code=422,
        code="request_validation_error",
    )

    winning_update_response = client.patch(
        path,
        headers=headers,
        json={
            "expected_version": 1,
            "email": "winner@example.com",
        },
    )
    assert winning_update_response.status_code == 200
    assert winning_update_response.json()["version"] == 2

    stale_update_response = client.patch(
        path,
        headers=headers,
        json={
            "expected_version": 1,
            "email": "loser@example.com",
        },
    )
    assert_problem(
        stale_update_response,
        status_code=409,
        code="patient_version_conflict",
    )

    archive_response = client.post(
        f"{path}/archive",
        headers=headers,
        json={"expected_version": 2},
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["version"] == 3

    stale_archive_response = client.post(
        f"{path}/archive",
        headers=headers,
        json={"expected_version": 2},
    )
    assert_problem(
        stale_archive_response,
        status_code=409,
        code="patient_version_conflict",
    )

    restore_response = client.post(
        f"{path}/restore",
        headers=headers,
        json={"expected_version": 3},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["version"] == 4

    stale_restore_response = client.post(
        f"{path}/restore",
        headers=headers,
        json={"expected_version": 3},
    )
    assert_problem(
        stale_restore_response,
        status_code=409,
        code="patient_version_conflict",
    )

    verification_response = client.get(path, headers=headers)
    assert verification_response.status_code == 200
    assert verification_response.json()["email"] == ("winner@example.com")
    assert verification_response.json()["status"] == "active"
    assert verification_response.json()["version"] == 4


def test_external_reference_conflict_is_tenant_local(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="reference-owner")
    first_tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="First Reference Clinic",
    )
    second_tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Second Reference Clinic",
    )
    headers = login_headers(client, owner)
    shared_reference = f"SHARED-{uuid4().hex}"

    first_response = create_patient(
        client,
        tenant_id=first_tenant.id,
        headers=headers,
        payload={
            "full_name": "First Patient",
            "external_reference": shared_reference,
        },
    )
    assert first_response.status_code == 201

    duplicate_response = create_patient(
        client,
        tenant_id=first_tenant.id,
        headers=headers,
        payload={
            "full_name": "Duplicate Patient",
            "external_reference": shared_reference,
        },
    )
    assert_problem(
        duplicate_response,
        status_code=409,
        code="patient_external_reference_conflict",
    )

    cross_tenant_response = create_patient(
        client,
        tenant_id=second_tenant.id,
        headers=headers,
        payload={
            "full_name": "Second Tenant Patient",
            "external_reference": shared_reference,
        },
    )
    assert cross_tenant_response.status_code == 201


def test_archived_patient_is_readable_but_not_updatable(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="archived-owner")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Archived Patient Clinic",
    )
    headers = login_headers(client, owner)

    created = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
    ).json()
    path = patient_path(tenant.id, created["id"])

    archive_response = client.post(
        f"{path}/archive",
        headers=headers,
        json={"expected_version": 1},
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"
    assert archive_response.json()["version"] == 2

    get_response = client.get(path, headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "archived"

    update_response = client.patch(
        path,
        headers=headers,
        json={
            "expected_version": 2,
            "email": "blocked@example.com",
        },
    )
    assert_problem(
        update_response,
        status_code=400,
        code="patient_invalid_update",
    )


def test_patient_application_errors_preserve_trace_identifiers(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="problem-owner")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Problem Details Clinic",
    )
    request_id = str(uuid4())
    correlation_id = str(uuid4())
    headers = login_headers(
        client,
        owner,
        request_id=request_id,
        correlation_id=correlation_id,
    )

    invalid_cursor_response = client.get(
        f"/api/v1/tenants/{tenant.id}/patients",
        headers=headers,
        params={"cursor": "not*valid"},
    )
    cursor_problem = assert_problem(
        invalid_cursor_response,
        status_code=400,
        code="patient_invalid_cursor",
    )
    assert cursor_problem["request_id"] == request_id
    assert cursor_problem["correlation_id"] == correlation_id
    assert invalid_cursor_response.headers[REQUEST_ID_HEADER] == request_id
    assert invalid_cursor_response.headers[CORRELATION_ID_HEADER] == correlation_id

    future_date = date.today() + timedelta(days=1)
    future_birth_response = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "full_name": "Future Patient",
            "date_of_birth": future_date.isoformat(),
        },
    )
    assert_problem(
        future_birth_response,
        status_code=400,
        code="patient_invalid_date_of_birth",
    )

    created = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
    ).json()
    no_op_response = client.patch(
        patient_path(tenant.id, created["id"]),
        headers=headers,
        json={"expected_version": 1},
    )
    assert_problem(
        no_op_response,
        status_code=400,
        code="patient_invalid_update",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"full_name": ""},
        {
            "full_name": "Invalid Email Patient",
            "email": "invalid-email",
        },
        {
            "full_name": "Unknown Field Patient",
            "unknown_field": "not-allowed",
        },
    ],
)
def test_patient_request_schema_validation_returns_problem_details(
    payload: dict[str, object],
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix=f"schema-{uuid4().hex[:8]}")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="Schema Validation Clinic",
    )
    headers = login_headers(client, owner)

    response = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload=payload,
    )

    assert_problem(
        response,
        status_code=422,
        code="request_validation_error",
    )


def test_delete_patient_operation_is_not_available(
    client: TestClient,
    patient_api_data_factory: PatientApiDataFactory,
) -> None:
    owner = patient_api_data_factory.create_user(email_prefix="delete-owner")
    tenant = patient_api_data_factory.create_tenant(
        owner=owner,
        name="No Delete Clinic",
    )
    headers = login_headers(client, owner)
    created = create_patient(
        client,
        tenant_id=tenant.id,
        headers=headers,
    ).json()

    response = client.delete(
        patient_path(tenant.id, created["id"]),
        headers=headers,
    )

    assert response.status_code == 405
