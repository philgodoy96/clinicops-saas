from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.patients.contracts import (
    ListPatientsCommand,
    PatientCursor,
    PatientRecord,
)
from clinicops.patients.enums import (
    PatientListStatus,
    PatientMutableField,
    PatientStatus,
)
from clinicops.patients.exceptions import (
    PatientExternalReferenceConflictError,
)
from clinicops.patients.models import Patient
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.validation import SEARCH_MAX_LENGTH
from clinicops.tenancy.models import Tenant


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _tenant(session: Session) -> Tenant:
    tenant = Tenant(name=f"Patient Repository Clinic {uuid4().hex}")
    session.add(tenant)
    session.flush()
    return tenant


def _patient(
    session: Session,
    *,
    tenant_id: UUID,
    full_name: str,
    status: PatientStatus = PatientStatus.ACTIVE,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    date_of_birth: date | None = None,
    email: str | None = None,
    phone: str | None = None,
    external_reference: str | None = None,
    patient_id: UUID | None = None,
) -> Patient:
    patient = Patient(
        id=patient_id or uuid4(),
        tenant_id=tenant_id,
        full_name=full_name,
        status=status,
        created_at=created_at or datetime.now(UTC),
        updated_at=updated_at or datetime.now(UTC),
        date_of_birth=date_of_birth,
        email=email,
        phone=phone,
        external_reference=external_reference,
    )
    session.add(patient)
    session.flush()
    return patient


def test_add_and_flush_persist_patient_without_owning_transaction(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    repository = PatientRepository(db_session)
    patient = Patient(
        tenant_id=tenant.id,
        full_name="Jordan Lee",
    )

    repository.add(patient)
    repository.flush()

    result = repository.get_by_id_for_tenant(
        tenant_id=tenant.id,
        patient_id=patient.id,
    )

    assert isinstance(result, PatientRecord)
    assert result.id == patient.id
    assert result.tenant_id == tenant.id
    assert result.status is PatientStatus.ACTIVE
    assert result.version == 1
    assert db_session.in_transaction() is True


def test_get_by_id_for_tenant_hides_foreign_patient(
    db_session: Session,
) -> None:
    owner_tenant = _tenant(db_session)
    foreign_tenant = _tenant(db_session)
    patient = _patient(
        db_session,
        tenant_id=owner_tenant.id,
        full_name="Morgan Ellis",
    )
    repository = PatientRepository(db_session)

    result = repository.get_by_id_for_tenant(
        tenant_id=foreign_tenant.id,
        patient_id=patient.id,
    )

    assert result is None


def test_default_listing_returns_only_active_tenant_patients(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    foreign_tenant = _tenant(db_session)
    active_patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Active Patient",
    )
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Archived Patient",
        status=PatientStatus.ARCHIVED,
    )
    _patient(
        db_session,
        tenant_id=foreign_tenant.id,
        full_name="Foreign Patient",
    )
    repository = PatientRepository(db_session)

    page = repository.list_page_for_tenant(ListPatientsCommand(tenant_id=tenant.id))

    assert [item.id for item in page.items] == [active_patient.id]
    assert page.next_cursor is None


@pytest.mark.parametrize(
    ("status_filter", "expected_statuses"),
    [
        (
            PatientListStatus.ACTIVE,
            {PatientStatus.ACTIVE},
        ),
        (
            PatientListStatus.ARCHIVED,
            {PatientStatus.ARCHIVED},
        ),
        (
            PatientListStatus.ALL,
            {
                PatientStatus.ACTIVE,
                PatientStatus.ARCHIVED,
            },
        ),
    ],
)
def test_listing_applies_status_filter(
    db_session: Session,
    status_filter: PatientListStatus,
    expected_statuses: set[PatientStatus],
) -> None:
    tenant = _tenant(db_session)
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Active Patient",
    )
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Archived Patient",
        status=PatientStatus.ARCHIVED,
    )
    repository = PatientRepository(db_session)

    page = repository.list_page_for_tenant(
        ListPatientsCommand(
            tenant_id=tenant.id,
            status=status_filter,
        )
    )

    assert {item.status for item in page.items} == expected_statuses


def test_listing_uses_created_at_and_uuid_descending_order(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    shared_timestamp = datetime(2026, 7, 23, 20, 0, tzinfo=UTC)
    lower_id = UUID("00000000-0000-0000-0000-000000000001")
    higher_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    newest = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Newest Patient",
        created_at=shared_timestamp + timedelta(minutes=1),
    )
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Lower UUID Patient",
        created_at=shared_timestamp,
        patient_id=lower_id,
    )
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Higher UUID Patient",
        created_at=shared_timestamp,
        patient_id=higher_id,
    )
    repository = PatientRepository(db_session)

    page = repository.list_page_for_tenant(
        ListPatientsCommand(
            tenant_id=tenant.id,
            status=PatientListStatus.ALL,
        )
    )

    assert [item.id for item in page.items] == [
        newest.id,
        higher_id,
        lower_id,
    ]


def test_listing_uses_stable_cursor_pagination(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    base_timestamp = datetime(2026, 7, 23, 20, 0, tzinfo=UTC)
    patients = [
        _patient(
            db_session,
            tenant_id=tenant.id,
            full_name=f"Patient {index}",
            created_at=base_timestamp - timedelta(minutes=index),
        )
        for index in range(5)
    ]
    repository = PatientRepository(db_session)

    first_page = repository.list_page_for_tenant(
        ListPatientsCommand(
            tenant_id=tenant.id,
            limit=2,
        )
    )

    assert [item.id for item in first_page.items] == [
        patients[0].id,
        patients[1].id,
    ]
    assert first_page.next_cursor == PatientCursor(
        created_at=patients[1].created_at,
        patient_id=patients[1].id,
    )

    second_page = repository.list_page_for_tenant(
        ListPatientsCommand(
            tenant_id=tenant.id,
            limit=2,
            cursor=first_page.next_cursor,
        )
    )

    assert [item.id for item in second_page.items] == [
        patients[2].id,
        patients[3].id,
    ]
    assert second_page.next_cursor == PatientCursor(
        created_at=patients[3].created_at,
        patient_id=patients[3].id,
    )

    final_page = repository.list_page_for_tenant(
        ListPatientsCommand(
            tenant_id=tenant.id,
            limit=2,
            cursor=second_page.next_cursor,
        )
    )

    assert [item.id for item in final_page.items] == [patients[4].id]
    assert final_page.next_cursor is None


@pytest.mark.parametrize(
    ("search", "matching_name"),
    [
        ("Jordan", "Jordan Search"),
        ("match@example.com", "Email Search"),
        ("555-0184", "Phone Search"),
        ("LEGACY-SEARCH", "Reference Search"),
    ],
)
def test_search_matches_approved_patient_fields(
    db_session: Session,
    search: str,
    matching_name: str,
) -> None:
    tenant = _tenant(db_session)
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Jordan Search",
    )
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Email Search",
        email="match@example.com",
    )
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Phone Search",
        phone="+1-202-555-0184",
    )
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Reference Search",
        external_reference="LEGACY-SEARCH",
    )
    repository = PatientRepository(db_session)

    page = repository.list_page_for_tenant(
        ListPatientsCommand(
            tenant_id=tenant.id,
            search=search,
        )
    )

    assert [item.full_name for item in page.items] == [matching_name]


def test_search_remains_tenant_scoped(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    foreign_tenant = _tenant(db_session)
    local_patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Scoped Match",
    )
    _patient(
        db_session,
        tenant_id=foreign_tenant.id,
        full_name="Scoped Match",
    )
    repository = PatientRepository(db_session)

    page = repository.list_page_for_tenant(
        ListPatientsCommand(
            tenant_id=tenant.id,
            search="Scoped Match",
        )
    )

    assert [item.id for item in page.items] == [local_patient.id]


@pytest.mark.parametrize(
    ("literal_search", "literal_name", "wildcard_name"),
    [
        ("100%", "Coverage 100% Complete", "Coverage 100X Complete"),
        ("A_B", "Code A_B", "Code AXB"),
    ],
)
def test_search_treats_sql_wildcards_as_literals(
    db_session: Session,
    literal_search: str,
    literal_name: str,
    wildcard_name: str,
) -> None:
    tenant = _tenant(db_session)
    literal_patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name=literal_name,
    )
    _patient(
        db_session,
        tenant_id=tenant.id,
        full_name=wildcard_name,
    )
    repository = PatientRepository(db_session)

    page = repository.list_page_for_tenant(
        ListPatientsCommand(
            tenant_id=tenant.id,
            search=literal_search,
        )
    )

    assert [item.id for item in page.items] == [literal_patient.id]


@pytest.mark.parametrize("limit", [0, 101])
def test_listing_rejects_out_of_bounds_limit(
    db_session: Session,
    limit: int,
) -> None:
    repository = PatientRepository(db_session)

    with pytest.raises(ValueError, match="between 1 and 100"):
        repository.list_page_for_tenant(
            ListPatientsCommand(
                tenant_id=uuid4(),
                limit=limit,
            )
        )


def test_listing_rejects_search_above_bound(
    db_session: Session,
) -> None:
    repository = PatientRepository(db_session)

    with pytest.raises(ValueError, match=str(SEARCH_MAX_LENGTH)):
        repository.list_page_for_tenant(
            ListPatientsCommand(
                tenant_id=uuid4(),
                search="s" * (SEARCH_MAX_LENGTH + 1),
            )
        )


def test_update_for_tenant_applies_only_explicit_fields(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    old_updated_at = datetime(2026, 7, 22, 20, 0, tzinfo=UTC)
    patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Jordan Lee",
        updated_at=old_updated_at,
        email="old@example.com",
        phone="+1-202-555-0100",
    )
    repository = PatientRepository(db_session)

    result = repository.update_for_tenant(
        tenant_id=tenant.id,
        patient_id=patient.id,
        expected_version=1,
        fields_to_update=frozenset(
            {
                PatientMutableField.EMAIL,
                PatientMutableField.PHONE,
            }
        ),
        email="new@example.com",
        phone=None,
    )

    assert isinstance(result, PatientRecord)
    assert result.full_name == "Jordan Lee"
    assert result.email == "new@example.com"
    assert result.phone is None
    assert result.version == 2
    assert result.updated_at > old_updated_at
    assert db_session.in_transaction() is True


@pytest.mark.parametrize(
    "wrong_scope",
    ["tenant", "version", "status"],
)
def test_update_for_tenant_returns_none_when_guard_fails(
    db_session: Session,
    wrong_scope: str,
) -> None:
    tenant = _tenant(db_session)
    foreign_tenant = _tenant(db_session)
    patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Guarded Patient",
        status=(PatientStatus.ARCHIVED if wrong_scope == "status" else PatientStatus.ACTIVE),
    )
    repository = PatientRepository(db_session)

    result = repository.update_for_tenant(
        tenant_id=(foreign_tenant.id if wrong_scope == "tenant" else tenant.id),
        patient_id=patient.id,
        expected_version=2 if wrong_scope == "version" else 1,
        fields_to_update=frozenset({PatientMutableField.EMAIL}),
        email="changed@example.com",
    )

    assert result is None


def test_archive_for_tenant_transitions_active_patient(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    old_updated_at = datetime(2026, 7, 22, 20, 0, tzinfo=UTC)
    patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Archive Patient",
        updated_at=old_updated_at,
    )
    repository = PatientRepository(db_session)

    result = repository.archive_for_tenant(
        tenant_id=tenant.id,
        patient_id=patient.id,
        expected_version=1,
    )

    assert isinstance(result, PatientRecord)
    assert result.status is PatientStatus.ARCHIVED
    assert result.version == 2
    assert result.updated_at > old_updated_at


@pytest.mark.parametrize(
    "wrong_scope",
    ["tenant", "version", "status"],
)
def test_archive_for_tenant_returns_none_when_guard_fails(
    db_session: Session,
    wrong_scope: str,
) -> None:
    tenant = _tenant(db_session)
    foreign_tenant = _tenant(db_session)
    patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Archive Guard Patient",
        status=(PatientStatus.ARCHIVED if wrong_scope == "status" else PatientStatus.ACTIVE),
    )
    repository = PatientRepository(db_session)

    result = repository.archive_for_tenant(
        tenant_id=(foreign_tenant.id if wrong_scope == "tenant" else tenant.id),
        patient_id=patient.id,
        expected_version=2 if wrong_scope == "version" else 1,
    )

    assert result is None


def test_restore_for_tenant_transitions_archived_patient(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    old_updated_at = datetime(2026, 7, 22, 20, 0, tzinfo=UTC)
    patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Restore Patient",
        status=PatientStatus.ARCHIVED,
        updated_at=old_updated_at,
    )
    repository = PatientRepository(db_session)

    result = repository.restore_for_tenant(
        tenant_id=tenant.id,
        patient_id=patient.id,
        expected_version=1,
    )

    assert isinstance(result, PatientRecord)
    assert result.status is PatientStatus.ACTIVE
    assert result.version == 2
    assert result.updated_at > old_updated_at


@pytest.mark.parametrize(
    "wrong_scope",
    ["tenant", "version", "status"],
)
def test_restore_for_tenant_returns_none_when_guard_fails(
    db_session: Session,
    wrong_scope: str,
) -> None:
    tenant = _tenant(db_session)
    foreign_tenant = _tenant(db_session)
    patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Restore Guard Patient",
        status=(PatientStatus.ACTIVE if wrong_scope == "status" else PatientStatus.ARCHIVED),
    )
    repository = PatientRepository(db_session)

    result = repository.restore_for_tenant(
        tenant_id=(foreign_tenant.id if wrong_scope == "tenant" else tenant.id),
        patient_id=patient.id,
        expected_version=2 if wrong_scope == "version" else 1,
    )

    assert result is None


def test_flush_translates_external_reference_conflict(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    repository = PatientRepository(db_session)
    repository.add(
        Patient(
            tenant_id=tenant.id,
            full_name="First Patient",
            external_reference="LEGACY-CONFLICT",
        )
    )
    repository.flush()
    repository.add(
        Patient(
            tenant_id=tenant.id,
            full_name="Second Patient",
            external_reference="LEGACY-CONFLICT",
        )
    )

    with pytest.raises(PatientExternalReferenceConflictError):
        repository.flush()


def test_update_translates_external_reference_conflict(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    first_patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="First Patient",
        external_reference="LEGACY-FIRST",
    )
    second_patient = _patient(
        db_session,
        tenant_id=tenant.id,
        full_name="Second Patient",
        external_reference="LEGACY-SECOND",
    )
    repository = PatientRepository(db_session)

    with pytest.raises(PatientExternalReferenceConflictError):
        repository.update_for_tenant(
            tenant_id=tenant.id,
            patient_id=second_patient.id,
            expected_version=1,
            fields_to_update=frozenset({PatientMutableField.EXTERNAL_REFERENCE}),
            external_reference=first_patient.external_reference,
        )


def test_external_reference_can_repeat_across_tenants(
    db_session: Session,
) -> None:
    first_tenant = _tenant(db_session)
    second_tenant = _tenant(db_session)
    repository = PatientRepository(db_session)

    repository.add(
        Patient(
            tenant_id=first_tenant.id,
            full_name="First Tenant Patient",
            external_reference="SHARED-REFERENCE",
        )
    )
    repository.add(
        Patient(
            tenant_id=second_tenant.id,
            full_name="Second Tenant Patient",
            external_reference="SHARED-REFERENCE",
        )
    )

    repository.flush()


def test_repository_exposes_only_tenant_scoped_domain_surface() -> None:
    forbidden_methods = {
        "commit",
        "delete",
        "get_all",
        "get_by_id",
        "list_all",
        "remove",
        "rollback",
        "save",
        "update",
    }

    for method_name in forbidden_methods:
        assert not hasattr(PatientRepository, method_name)
