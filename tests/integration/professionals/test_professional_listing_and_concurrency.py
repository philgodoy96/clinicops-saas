from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction
from clinicops.audit.models import AuditLogEntry
from clinicops.db.session import get_engine
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.models import Professional
from clinicops.professionals.validation import SEARCH_MAX_LENGTH
from clinicops.tenancy.models import Membership, MembershipStatus, TenantRole
from tests.integration.professionals.test_professional_api import (
    PROFESSIONAL_ARCHIVE_PATH,
    PROFESSIONAL_LINK_PATH,
    PROFESSIONAL_RESTORE_PATH,
    PROFESSIONAL_UNLINK_PATH,
    ProfessionalApiDataFactory,
    assert_problem,
    create_professional,
    login_headers,
    professional_path,
    professionals_collection_path,
)

SHARED_CREATED_AT = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
LOWER_TIE_ID = UUID("00000000-0000-4000-8000-000000000001")
HIGHER_TIE_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


@pytest.fixture
def professional_api_data_factory() -> Iterator[ProfessionalApiDataFactory]:
    session = Session(get_engine())
    factory = ProfessionalApiDataFactory(session)

    try:
        yield factory
    finally:
        factory.cleanup()
        session.close()


def _archive_path(tenant_id: UUID, professional_id: str) -> str:
    return PROFESSIONAL_ARCHIVE_PATH.format(
        tenant_id=tenant_id,
        professional_id=professional_id,
    )


def _restore_path(tenant_id: UUID, professional_id: str) -> str:
    return PROFESSIONAL_RESTORE_PATH.format(
        tenant_id=tenant_id,
        professional_id=professional_id,
    )


def _link_path(tenant_id: UUID, professional_id: str) -> str:
    return PROFESSIONAL_LINK_PATH.format(
        tenant_id=tenant_id,
        professional_id=professional_id,
    )


def _unlink_path(tenant_id: UUID, professional_id: str) -> str:
    return PROFESSIONAL_UNLINK_PATH.format(
        tenant_id=tenant_id,
        professional_id=professional_id,
    )


def _persist_professionals(
    *,
    tenant_id: UUID,
    rows: list[dict[str, object]],
) -> list[UUID]:
    session = Session(get_engine())
    try:
        professionals: list[Professional] = []
        for row in rows:
            created_at = cast(datetime, row["created_at"])
            professional = Professional(
                id=cast(UUID, row["id"]) if "id" in row else uuid4(),
                tenant_id=tenant_id,
                full_name=cast(str, row["full_name"]),
                specialty=cast(str | None, row.get("specialty")),
                registration_number=cast(str | None, row.get("registration_number")),
                email=cast(str | None, row.get("email")),
                phone=cast(str | None, row.get("phone")),
                external_reference=cast(str | None, row.get("external_reference")),
                status=cast(
                    ProfessionalStatus,
                    row.get("status", ProfessionalStatus.ACTIVE),
                ),
            )
            professional.created_at = created_at
            professional.updated_at = created_at
            session.add(professional)
            professionals.append(professional)

        session.commit()
        return [professional.id for professional in professionals]
    finally:
        session.close()


def _membership_status(membership_id: UUID) -> MembershipStatus:
    session = Session(get_engine())
    try:
        membership = session.get(Membership, membership_id)
        assert membership is not None
        return membership.status
    finally:
        session.close()


def _audit_entry_count(
    *,
    tenant_id: UUID,
    professional_id: str,
) -> int:
    session = Session(get_engine())
    try:
        count = session.scalar(
            select(func.count())
            .select_from(AuditLogEntry)
            .where(
                AuditLogEntry.tenant_id == tenant_id,
                AuditLogEntry.resource_type == "professional",
                AuditLogEntry.resource_id == professional_id,
            )
        )
        assert count is not None
        return count
    finally:
        session.close()


def _list_ids(page: dict[str, object]) -> list[str]:
    items = cast(list[dict[str, object]], page["items"])
    return [cast(str, item["id"]) for item in items]


def _create_professional(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
    payload: dict[str, object] | None = None,
) -> Response:
    body: dict[str, object] = {
        "external_reference": f"REF-{uuid4().hex}",
        "email": f"professional-{uuid4().hex}@example.com",
        "registration_number": f"REG-{uuid4().hex[:10]}",
        "phone": f"+1-202-555-{uuid4().int % 10000:04d}",
    }
    if payload is not None:
        body.update(payload)
    return create_professional(
        client,
        tenant_id=tenant_id,
        headers=headers,
        payload=body,
    )


def test_listing_defaults_to_active_page_of_fifty(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="listing-default-owner",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Listing Default Clinic",
    )
    headers = login_headers(client, owner)
    collection_path = professionals_collection_path(tenant.id)

    active_rows = [
        {
            "id": uuid4(),
            "full_name": f"Active Professional {index:02d}",
            "created_at": SHARED_CREATED_AT - timedelta(seconds=index),
        }
        for index in range(51)
    ]
    active_ids = _persist_professionals(
        tenant_id=tenant.id,
        rows=active_rows,
    )
    archived_ids = _persist_professionals(
        tenant_id=tenant.id,
        rows=[
            {
                "full_name": "Archived Outside Default Scope",
                "created_at": SHARED_CREATED_AT + timedelta(hours=1),
                "status": ProfessionalStatus.ARCHIVED,
            }
        ],
    )

    response = client.get(collection_path, headers=headers)
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"items", "next_cursor"}
    assert len(body["items"]) == 50
    assert body["next_cursor"] is not None
    assert all(item["status"] == "active" for item in body["items"])
    assert {item["id"] for item in body["items"]}.isdisjoint(
        {str(professional_id) for professional_id in archived_ids}
    )

    ordered = sorted(
        zip(
            active_ids,
            [cast(datetime, row["created_at"]) for row in active_rows],
            strict=True,
        ),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    expected_order = [str(professional_id) for professional_id, _ in ordered[:50]]
    assert _list_ids(body) == expected_order


def test_keyset_pagination_is_complete_ordered_and_opaque(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="listing-pagination-owner",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Listing Pagination Clinic",
    )
    headers = login_headers(client, owner)
    collection_path = professionals_collection_path(tenant.id)

    persisted = _persist_professionals(
        tenant_id=tenant.id,
        rows=[
            {
                "id": uuid4(),
                "full_name": "Newest Professional",
                "created_at": SHARED_CREATED_AT + timedelta(minutes=2),
                "email": "newest@example.com",
                "external_reference": "EXT-NEWEST",
            },
            {
                "id": uuid4(),
                "full_name": "Middle Professional",
                "created_at": SHARED_CREATED_AT + timedelta(minutes=1),
            },
            {
                "id": HIGHER_TIE_ID,
                "full_name": "Higher Tie Professional",
                "created_at": SHARED_CREATED_AT,
            },
            {
                "id": LOWER_TIE_ID,
                "full_name": "Lower Tie Professional",
                "created_at": SHARED_CREATED_AT,
            },
            {
                "id": uuid4(),
                "full_name": "Older Professional",
                "created_at": SHARED_CREATED_AT - timedelta(minutes=1),
            },
            {
                "id": uuid4(),
                "full_name": "Oldest Professional",
                "created_at": SHARED_CREATED_AT - timedelta(minutes=2),
            },
        ],
    )
    expected_order = [
        str(persisted[0]),
        str(persisted[1]),
        str(HIGHER_TIE_ID),
        str(LOWER_TIE_ID),
        str(persisted[4]),
        str(persisted[5]),
    ]

    collected: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor

        response = client.get(
            collection_path,
            headers=headers,
            params=params,
        )
        assert response.status_code == 200
        page = response.json()
        pages += 1

        page_ids = _list_ids(page)
        assert len(page_ids) <= 2
        collected.extend(page_ids)

        next_cursor = page["next_cursor"]
        if next_cursor is None:
            break

        assert isinstance(next_cursor, str)
        assert next_cursor
        assert str(tenant.id) not in next_cursor
        assert "Newest Professional" not in next_cursor
        assert "newest@example.com" not in next_cursor
        assert "EXT-NEWEST" not in next_cursor
        cursor = next_cursor

    assert pages >= 3
    assert collected == expected_order
    assert len(collected) == len(set(collected))
    assert set(collected) == set(expected_order)


def test_invalid_cursor_returns_safe_problem_details(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="listing-cursor-owner",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Listing Cursor Clinic",
    )
    headers = login_headers(client, owner)

    response = client.get(
        professionals_collection_path(tenant.id),
        headers=headers,
        params={"cursor": "not*valid"},
    )
    assert_problem(
        response,
        status_code=400,
        code="professional_invalid_cursor",
    )


def test_lifecycle_filters_and_archived_mutation_guards(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="listing-lifecycle-owner",
    )
    link_user = professional_api_data_factory.create_user(
        email_prefix="listing-lifecycle-member",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Listing Lifecycle Clinic",
    )
    link_membership = professional_api_data_factory.add_membership(
        tenant=tenant,
        user=link_user,
        role=TenantRole.STAFF,
    )
    headers = login_headers(client, owner)
    collection_path = professionals_collection_path(tenant.id)

    active = _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={"full_name": "Active Listed Professional"},
    ).json()
    to_archive = _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "full_name": "Archived Listed Professional",
            "email": "archived-listed@example.com",
        },
    ).json()

    link_response = client.post(
        _link_path(tenant.id, to_archive["id"]),
        headers=headers,
        json={
            "membership_id": str(link_membership.id),
            "expected_version": 1,
        },
    )
    assert link_response.status_code == 200
    assert link_response.json()["membership_id"] == str(link_membership.id)
    assert link_response.json()["version"] == 2
    membership_status_before = _membership_status(link_membership.id)
    assert membership_status_before is MembershipStatus.ACTIVE

    archive_response = client.post(
        _archive_path(tenant.id, to_archive["id"]),
        headers=headers,
        json={"expected_version": 2},
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"
    assert archive_response.json()["version"] == 3
    assert archive_response.json()["membership_id"] == str(link_membership.id)

    default_response = client.get(collection_path, headers=headers)
    active_only_response = client.get(
        collection_path,
        headers=headers,
        params={"status": "active"},
    )
    archived_response = client.get(
        collection_path,
        headers=headers,
        params={"status": "archived"},
    )
    all_response = client.get(
        collection_path,
        headers=headers,
        params={"status": "all"},
    )

    assert {item["id"] for item in default_response.json()["items"]} == {active["id"]}
    assert {item["id"] for item in active_only_response.json()["items"]} == {active["id"]}
    assert {item["id"] for item in archived_response.json()["items"]} == {to_archive["id"]}
    assert {item["id"] for item in all_response.json()["items"]} == {
        active["id"],
        to_archive["id"],
    }

    item_path = professional_path(tenant.id, to_archive["id"])
    archived_get = client.get(item_path, headers=headers)
    assert archived_get.status_code == 200
    assert archived_get.json()["status"] == "archived"
    assert archived_get.json()["membership_id"] == str(link_membership.id)

    assert_problem(
        client.patch(
            item_path,
            headers=headers,
            json={
                "expected_version": 3,
                "specialty": "Blocked Specialty",
            },
        ),
        status_code=400,
        code="professional_invalid_update",
    )
    assert_problem(
        client.post(
            _link_path(tenant.id, to_archive["id"]),
            headers=headers,
            json={
                "membership_id": str(link_membership.id),
                "expected_version": 3,
            },
        ),
        status_code=409,
        code="professional_already_archived",
    )
    assert_problem(
        client.post(
            _unlink_path(tenant.id, to_archive["id"]),
            headers=headers,
            json={"expected_version": 3},
        ),
        status_code=409,
        code="professional_already_archived",
    )

    restore_response = client.post(
        _restore_path(tenant.id, to_archive["id"]),
        headers=headers,
        json={"expected_version": 3},
    )
    assert restore_response.status_code == 200
    restored = restore_response.json()
    assert restored["status"] == "active"
    assert restored["version"] == 4
    assert restored["membership_id"] == str(link_membership.id)
    assert _membership_status(link_membership.id) is membership_status_before

    restored_list = client.get(collection_path, headers=headers)
    assert {item["id"] for item in restored_list.json()["items"]} == {
        active["id"],
        to_archive["id"],
    }


@pytest.mark.parametrize(
    ("payload", "search"),
    [
        ({"full_name": "Morgan Reed"}, "morgan"),
        ({"full_name": "Specialty Target", "specialty": "Orthodontics"}, "ORTHO"),
        (
            {
                "full_name": "Registration Target",
                "registration_number": "DDS-48291",
            },
            "48291",
        ),
        (
            {
                "full_name": "Email Target",
                "email": "lookup@example.com",
            },
            "LOOKUP@EXAMPLE.COM",
        ),
        (
            {
                "full_name": "Phone Target",
                "phone": "+1-202-555-0199",
            },
            "555-0199",
        ),
        (
            {
                "full_name": "Reference Target",
                "external_reference": "PROVIDER-100",
            },
            "provider-100",
        ),
    ],
)
def test_search_matches_supported_fields_case_insensitively(
    payload: dict[str, object],
    search: str,
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix=f"search-field-{uuid4().hex[:8]}",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Search Field Clinic",
    )
    headers = login_headers(client, owner)

    _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={"full_name": "Noise Professional", "specialty": "General"},
    )
    created = _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload=payload,
    )
    assert created.status_code == 201

    response = client.get(
        professionals_collection_path(tenant.id),
        headers=headers,
        params={"search": search},
    )
    assert response.status_code == 200
    assert _list_ids(response.json()) == [created.json()["id"]]


@pytest.mark.parametrize(
    ("stored_name", "noise_name", "search"),
    [
        ("Coverage 100% Complete", "Coverage 100X Complete", "%"),
        ("Code A_B", "Code AXB", "_"),
    ],
)
def test_search_treats_percent_and_underscore_as_literals(
    stored_name: str,
    noise_name: str,
    search: str,
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix=f"search-literal-{uuid4().hex[:8]}",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Search Literal Clinic",
    )
    headers = login_headers(client, owner)

    matching = _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={"full_name": stored_name},
    ).json()
    _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={"full_name": noise_name},
    )

    response = client.get(
        professionals_collection_path(tenant.id),
        headers=headers,
        params={"search": search},
    )
    assert response.status_code == 200
    assert _list_ids(response.json()) == [matching["id"]]


def test_blank_and_oversized_search_boundaries(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="search-boundary-owner",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Search Boundary Clinic",
    )
    headers = login_headers(client, owner)
    collection_path = professionals_collection_path(tenant.id)

    first = _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={"full_name": "Boundary First"},
    ).json()
    second = _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={"full_name": "Boundary Second"},
    ).json()

    unfiltered = client.get(collection_path, headers=headers)
    blank = client.get(
        collection_path,
        headers=headers,
        params={"search": "   "},
    )
    assert blank.status_code == 200
    assert _list_ids(blank.json()) == _list_ids(unfiltered.json())
    assert set(_list_ids(blank.json())) == {first["id"], second["id"]}

    oversized = client.get(
        collection_path,
        headers=headers,
        params={"search": "s" * (SEARCH_MAX_LENGTH + 1)},
    )
    assert_problem(
        oversized,
        status_code=422,
        code="request_validation_error",
    )


def test_search_never_crosses_tenant_boundaries(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="search-isolation-owner",
    )
    first_tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Search Isolation First",
    )
    second_tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Search Isolation Second",
    )
    headers = login_headers(client, owner)
    marker = f"CROSS-TENANT-{uuid4().hex}"

    foreign = _create_professional(
        client,
        tenant_id=second_tenant.id,
        headers=headers,
        payload={
            "full_name": "Foreign Search Professional",
            "external_reference": marker,
        },
    ).json()
    local = _create_professional(
        client,
        tenant_id=first_tenant.id,
        headers=headers,
        payload={
            "full_name": "Local Search Professional",
            "external_reference": f"LOCAL-{uuid4().hex}",
        },
    ).json()

    response = client.get(
        professionals_collection_path(first_tenant.id),
        headers=headers,
        params={"search": marker},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert foreign["id"] not in response.text
    assert local["id"] not in _list_ids(response.json())


def test_optimistic_concurrency_rejects_stale_mutations_without_audit(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="concurrency-owner",
    )
    first_member = professional_api_data_factory.create_user(
        email_prefix="concurrency-member-a",
    )
    second_member = professional_api_data_factory.create_user(
        email_prefix="concurrency-member-b",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="Concurrency Clinic",
    )
    first_membership = professional_api_data_factory.add_membership(
        tenant=tenant,
        user=first_member,
        role=TenantRole.STAFF,
    )
    second_membership = professional_api_data_factory.add_membership(
        tenant=tenant,
        user=second_member,
        role=TenantRole.STAFF,
    )
    headers = login_headers(client, owner)

    created = _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "full_name": "Concurrency Professional",
            "specialty": "Dentistry",
            "email": "concurrency@example.com",
        },
    ).json()
    professional_id = created["id"]
    item_path = professional_path(tenant.id, professional_id)
    assert created["version"] == 1

    winning_update = client.patch(
        item_path,
        headers=headers,
        json={
            "expected_version": 1,
            "specialty": "Orthodontics",
        },
    )
    assert winning_update.status_code == 200
    assert winning_update.json()["specialty"] == "Orthodontics"
    assert winning_update.json()["version"] == 2
    audit_after_update = _audit_entry_count(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )

    stale_update = client.patch(
        item_path,
        headers=headers,
        json={
            "expected_version": 1,
            "specialty": "Periodontics",
        },
    )
    assert_problem(
        stale_update,
        status_code=409,
        code="professional_version_conflict",
    )
    assert (
        _audit_entry_count(
            tenant_id=tenant.id,
            professional_id=professional_id,
        )
        == audit_after_update
    )

    verified = client.get(item_path, headers=headers).json()
    assert verified["specialty"] == "Orthodontics"
    assert verified["version"] == 2

    archive = client.post(
        _archive_path(tenant.id, professional_id),
        headers=headers,
        json={"expected_version": 2},
    )
    assert archive.status_code == 200
    assert archive.json()["status"] == "archived"
    assert archive.json()["version"] == 3
    audit_after_archive = _audit_entry_count(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )

    stale_archive = client.post(
        _archive_path(tenant.id, professional_id),
        headers=headers,
        json={"expected_version": 2},
    )
    assert_problem(
        stale_archive,
        status_code=409,
        code="professional_version_conflict",
    )
    assert (
        _audit_entry_count(
            tenant_id=tenant.id,
            professional_id=professional_id,
        )
        == audit_after_archive
    )

    restore = client.post(
        _restore_path(tenant.id, professional_id),
        headers=headers,
        json={"expected_version": 3},
    )
    assert restore.status_code == 200
    assert restore.json()["status"] == "active"
    assert restore.json()["version"] == 4
    audit_after_restore = _audit_entry_count(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )

    stale_restore = client.post(
        _restore_path(tenant.id, professional_id),
        headers=headers,
        json={"expected_version": 3},
    )
    assert_problem(
        stale_restore,
        status_code=409,
        code="professional_version_conflict",
    )
    assert (
        _audit_entry_count(
            tenant_id=tenant.id,
            professional_id=professional_id,
        )
        == audit_after_restore
    )

    link = client.post(
        _link_path(tenant.id, professional_id),
        headers=headers,
        json={
            "membership_id": str(first_membership.id),
            "expected_version": 4,
        },
    )
    assert link.status_code == 200
    assert link.json()["membership_id"] == str(first_membership.id)
    assert link.json()["version"] == 5
    audit_after_link = _audit_entry_count(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )

    stale_link = client.post(
        _link_path(tenant.id, professional_id),
        headers=headers,
        json={
            "membership_id": str(second_membership.id),
            "expected_version": 4,
        },
    )
    assert_problem(
        stale_link,
        status_code=409,
        code="professional_version_conflict",
    )
    assert (
        _audit_entry_count(
            tenant_id=tenant.id,
            professional_id=professional_id,
        )
        == audit_after_link
    )
    assert client.get(item_path, headers=headers).json()["membership_id"] == str(
        first_membership.id
    )
    assert client.get(item_path, headers=headers).json()["version"] == 5

    unlink = client.post(
        _unlink_path(tenant.id, professional_id),
        headers=headers,
        json={"expected_version": 5},
    )
    assert unlink.status_code == 200
    assert unlink.json()["membership_id"] is None
    assert unlink.json()["version"] == 6
    audit_after_unlink = _audit_entry_count(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )

    stale_unlink = client.post(
        _unlink_path(tenant.id, professional_id),
        headers=headers,
        json={"expected_version": 5},
    )
    assert_problem(
        stale_unlink,
        status_code=409,
        code="professional_version_conflict",
    )
    assert (
        _audit_entry_count(
            tenant_id=tenant.id,
            professional_id=professional_id,
        )
        == audit_after_unlink
    )

    final_state = client.get(item_path, headers=headers).json()
    assert final_state["specialty"] == "Orthodontics"
    assert final_state["membership_id"] is None
    assert final_state["status"] == "active"
    assert final_state["version"] == 6

    audit_actions = {
        item["action"]
        for item in client.get(
            f"/api/v1/tenants/{tenant.id}/audit-logs",
            headers=headers,
            params={
                "resource_type": "professional",
                "resource_id": professional_id,
            },
        ).json()["items"]
    }
    assert audit_actions == {
        AuditAction.PROFESSIONAL_CREATED.value,
        AuditAction.PROFESSIONAL_UPDATED.value,
        AuditAction.PROFESSIONAL_ARCHIVED.value,
        AuditAction.PROFESSIONAL_RESTORED.value,
        AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value,
        AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value,
    }


def test_normalized_no_op_patch_does_not_mutate_or_audit(
    client: TestClient,
    professional_api_data_factory: ProfessionalApiDataFactory,
) -> None:
    owner = professional_api_data_factory.create_user(
        email_prefix="noop-owner",
    )
    tenant = professional_api_data_factory.create_tenant(
        owner=owner,
        name="No-op Clinic",
    )
    headers = login_headers(client, owner)

    created = _create_professional(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "full_name": "No Op Professional",
            "specialty": "Dentistry",
        },
    ).json()
    professional_id = created["id"]
    item_path = professional_path(tenant.id, professional_id)
    audit_before = _audit_entry_count(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )

    missing_fields = client.patch(
        item_path,
        headers=headers,
        json={"expected_version": 1},
    )
    assert_problem(
        missing_fields,
        status_code=400,
        code="professional_invalid_update",
    )

    normalized_no_op = client.patch(
        item_path,
        headers=headers,
        json={
            "expected_version": 1,
            "full_name": "  No Op Professional  ",
            "specialty": " Dentistry ",
        },
    )
    assert_problem(
        normalized_no_op,
        status_code=400,
        code="professional_invalid_update",
    )

    verified = client.get(item_path, headers=headers).json()
    assert verified["version"] == 1
    assert verified["full_name"] == "No Op Professional"
    assert verified["specialty"] == "Dentistry"
    assert (
        _audit_entry_count(
            tenant_id=tenant.id,
            professional_id=professional_id,
        )
        == audit_before
    )
