from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from clinicops.audit.context import AuditRecordingContext
from clinicops.patients.contracts import (
    CreatePatientCommand,
    ListPatientsCommand,
    PatientCursor,
    PatientPage,
    PatientRecord,
    UpdatePatientCommand,
    UpdatedPatient,
)
from clinicops.patients.enums import (
    PatientListStatus,
    PatientMutableField,
    PatientStatus,
)


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=uuid4(),
        role="owner",
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
    )


def _patient_record() -> PatientRecord:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    return PatientRecord(
        id=uuid4(),
        tenant_id=uuid4(),
        full_name="Jordan Lee",
        date_of_birth=date(1992, 8, 14),
        email="jordan.lee@example.com",
        phone="+1-202-555-0184",
        external_reference="LEGACY-10482",
        status=PatientStatus.ACTIVE,
        version=3,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_patient_record_is_immutable() -> None:
    patient = _patient_record()

    with pytest.raises(FrozenInstanceError):
        patient.full_name = "Changed Name"  # type: ignore[misc]


def test_patient_page_uses_immutable_items() -> None:
    patient = _patient_record()
    cursor = PatientCursor(
        created_at=patient.created_at,
        patient_id=patient.id,
    )
    page = PatientPage(
        items=(patient,),
        next_cursor=cursor,
    )

    assert page.items == (patient,)
    assert page.next_cursor == cursor


def test_create_patient_command_preserves_optional_nulls() -> None:
    command = CreatePatientCommand(
        tenant_id=uuid4(),
        full_name="Jordan Lee",
        audit_context=_audit_context(),
    )

    assert command.date_of_birth is None
    assert command.email is None
    assert command.phone is None
    assert command.external_reference is None


def test_list_patients_command_has_approved_defaults() -> None:
    command = ListPatientsCommand(tenant_id=uuid4())

    assert command.limit == 50
    assert command.status is PatientListStatus.ACTIVE
    assert command.search is None
    assert command.cursor is None


def test_update_command_distinguishes_omitted_from_explicit_null() -> None:
    tenant_id = uuid4()
    patient_id = uuid4()

    omitted = UpdatePatientCommand(
        tenant_id=tenant_id,
        patient_id=patient_id,
        expected_version=3,
        fields_to_update=frozenset(),
        audit_context=_audit_context(),
        email=None,
    )
    explicit_null = UpdatePatientCommand(
        tenant_id=tenant_id,
        patient_id=patient_id,
        expected_version=3,
        fields_to_update=frozenset({PatientMutableField.EMAIL}),
        audit_context=_audit_context(),
        email=None,
    )

    assert PatientMutableField.EMAIL not in omitted.fields_to_update
    assert PatientMutableField.EMAIL in explicit_null.fields_to_update
    assert omitted.email is None
    assert explicit_null.email is None


def test_mutation_command_retains_immutable_audit_context() -> None:
    context = _audit_context()
    command = CreatePatientCommand(
        tenant_id=uuid4(),
        full_name="Jordan Lee",
        audit_context=context,
    )

    assert command.audit_context is context

    with pytest.raises(FrozenInstanceError):
        command.audit_context = _audit_context()  # type: ignore[misc]


def test_updated_patient_preserves_deterministic_changed_fields() -> None:
    patient = _patient_record()
    result = UpdatedPatient(
        patient=patient,
        changed_fields=(
            PatientMutableField.EMAIL,
            PatientMutableField.PHONE,
        ),
    )

    assert result.patient is patient
    assert result.changed_fields == (
        PatientMutableField.EMAIL,
        PatientMutableField.PHONE,
    )
