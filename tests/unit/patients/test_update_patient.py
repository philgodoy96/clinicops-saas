from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import (
    RecordAuditLogCommand,
    RecordedAuditLog,
)
from clinicops.audit.enums import AuditSource
from clinicops.audit.recording import AuditRecorder
from clinicops.patients.contracts import (
    PatientRecord,
    UpdatePatientCommand,
)
from clinicops.patients.enums import (
    PatientMutableField,
    PatientStatus,
)
from clinicops.patients.exceptions import (
    PatientExternalReferenceConflictError,
    PatientInvalidDateOfBirthError,
    PatientInvalidUpdateError,
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.update_patient import (
    UpdatePatientService,
)

_FIXED_AUDIT_RECORDED_AT = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
_PII_METADATA_KEYS = frozenset(
    {
        "full_name",
        "email",
        "phone",
        "date_of_birth",
        "external_reference",
    }
)


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class RecordingAuditRecorder:
    def __init__(self) -> None:
        self.sessions: list[Session] = []
        self.commands: list[RecordAuditLogCommand] = []

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        self.sessions.append(session)
        self.commands.append(command)

        return RecordedAuditLog(
            audit_log_id=uuid4(),
            created=True,
            recorded_at=_FIXED_AUDIT_RECORDED_AT,
        )


class FailingAuditRecorder:
    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError("audit recording failed")


class RecordingPatientRepository:
    def __init__(
        self,
        *,
        reads: list[PatientRecord | None],
        update_result: PatientRecord | None = None,
        update_error: Exception | None = None,
    ) -> None:
        self.reads = list(reads)
        self.update_result = update_result
        self.update_error = update_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.update_calls: list[dict[str, object]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
    ) -> PatientRecord | None:
        self.get_calls.append((tenant_id, patient_id))
        if not self.reads:
            raise AssertionError("No configured patient read remains.")
        return self.reads.pop(0)

    def update_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
        expected_version: int,
        fields_to_update: frozenset[PatientMutableField],
        full_name: str | None = None,
        date_of_birth: date | None = None,
        email: str | None = None,
        phone: str | None = None,
        external_reference: str | None = None,
    ) -> PatientRecord | None:
        self.update_calls.append(
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
                "expected_version": expected_version,
                "fields_to_update": fields_to_update,
                "full_name": full_name,
                "date_of_birth": date_of_birth,
                "email": email,
                "phone": phone,
                "external_reference": external_reference,
            }
        )
        if self.update_error is not None:
            raise self.update_error
        return self.update_result


def _patient_record(
    *,
    tenant_id: UUID | None = None,
    patient_id: UUID | None = None,
    status: PatientStatus = PatientStatus.ACTIVE,
    version: int = 3,
    full_name: str = "Jordan Lee",
    date_of_birth: date | None = date(1992, 8, 14),
    email: str | None = "old@example.com",
    phone: str | None = "+1-202-555-0100",
    external_reference: str | None = "LEGACY-100",
) -> PatientRecord:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    return PatientRecord(
        id=patient_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        full_name=full_name,
        date_of_birth=date_of_birth,
        email=email,
        phone=phone,
        external_reference=external_reference,
        status=status,
        version=version,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=uuid4(),
        role="owner",
        request_id="request-patient-update",
        correlation_id="correlation-patient-update",
    )


def _service(
    repository: RecordingPatientRepository,
    *,
    session: object | None = None,
    audit_recorder: RecordingAuditRecorder | FailingAuditRecorder | None = None,
) -> tuple[
    UpdatePatientService,
    object,
    RecordingAuditRecorder | FailingAuditRecorder,
]:
    resolved_session = object() if session is None else session
    resolved_recorder = RecordingAuditRecorder() if audit_recorder is None else audit_recorder
    service = UpdatePatientService(
        cast(PatientRepository, repository),
        cast(Session, resolved_session),
        cast(AuditRecorder, resolved_recorder),
    )
    return service, resolved_session, resolved_recorder


def _assert_no_pii_in_metadata(metadata: Mapping[str, object]) -> None:
    assert _PII_METADATA_KEYS.isdisjoint(metadata)
    serialized_values = [str(value) for value in metadata.values()]
    for value in serialized_values:
        assert "@" not in value
        assert "Jordan" not in value
        assert "+1-" not in value
        assert "LEGACY" not in value


def test_update_patient_normalizes_and_updates_changed_fields() -> None:
    current = _patient_record()
    updated = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
        full_name="Jordan  Lee",
        email="new@example.com",
        phone=None,
    )
    repository = RecordingPatientRepository(
        reads=[current],
        update_result=updated,
    )
    service, session, recorder = _service(repository)

    result = service.execute(
        UpdatePatientCommand(
            tenant_id=current.tenant_id,
            patient_id=current.id,
            expected_version=3,
            fields_to_update=frozenset(
                {
                    PatientMutableField.FULL_NAME,
                    PatientMutableField.EMAIL,
                    PatientMutableField.PHONE,
                }
            ),
            full_name="  Jordan  Lee  ",
            email="  New@Example.COM  ",
            phone=None,
            audit_context=_audit_context(),
        )
    )

    assert result.patient is updated
    assert result.changed_fields == (
        PatientMutableField.FULL_NAME,
        PatientMutableField.EMAIL,
        PatientMutableField.PHONE,
    )
    assert repository.update_calls == [
        {
            "tenant_id": current.tenant_id,
            "patient_id": current.id,
            "expected_version": 3,
            "fields_to_update": frozenset(
                {
                    PatientMutableField.FULL_NAME,
                    PatientMutableField.EMAIL,
                    PatientMutableField.PHONE,
                }
            ),
            "full_name": "Jordan  Lee",
            "date_of_birth": None,
            "email": "new@example.com",
            "phone": None,
            "external_reference": None,
        }
    ]

    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.sessions == [cast(Session, session)]
    assert len(recorder.commands) == 1
    command = recorder.commands[0]
    assert command.action == AuditAction.PATIENT_UPDATED.value
    assert command.resource_type == AuditResourceType.PATIENT.value
    assert command.resource_id == str(updated.id)
    assert command.source is AuditSource.HTTP
    assert command.request_id == "request-patient-update"
    assert command.correlation_id == "correlation-patient-update"
    assert command.metadata == {
        "version": 4,
        "changed_fields": ["full_name", "email", "phone"],
    }
    assert command.idempotency_key == f"patient-updated:{updated.id}:4"
    _assert_no_pii_in_metadata(command.metadata)


def test_update_patient_sends_only_values_that_changed() -> None:
    current = _patient_record(
        email="same@example.com",
        phone="+1-202-555-0100",
    )
    updated = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
        email="same@example.com",
        phone="+1-202-555-0200",
    )
    repository = RecordingPatientRepository(
        reads=[current],
        update_result=updated,
    )
    service, _, recorder = _service(repository)

    result = service.execute(
        UpdatePatientCommand(
            tenant_id=current.tenant_id,
            patient_id=current.id,
            expected_version=3,
            fields_to_update=frozenset(
                {
                    PatientMutableField.EMAIL,
                    PatientMutableField.PHONE,
                }
            ),
            email=" SAME@EXAMPLE.COM ",
            phone=" +1-202-555-0200 ",
            audit_context=_audit_context(),
        )
    )

    assert result.changed_fields == (PatientMutableField.PHONE,)
    assert repository.update_calls[0]["fields_to_update"] == frozenset({PatientMutableField.PHONE})
    assert repository.update_calls[0]["email"] == "same@example.com"
    assert repository.update_calls[0]["phone"] == "+1-202-555-0200"

    assert isinstance(recorder, RecordingAuditRecorder)
    assert len(recorder.commands) == 1
    assert recorder.commands[0].metadata["changed_fields"] == ["phone"]
    _assert_no_pii_in_metadata(recorder.commands[0].metadata)


def test_update_patient_explicitly_clears_optional_field() -> None:
    current = _patient_record(email="old@example.com")
    updated = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
        email=None,
    )
    repository = RecordingPatientRepository(
        reads=[current],
        update_result=updated,
    )
    service, _, recorder = _service(repository)

    result = service.execute(
        UpdatePatientCommand(
            tenant_id=current.tenant_id,
            patient_id=current.id,
            expected_version=3,
            fields_to_update=frozenset({PatientMutableField.EMAIL}),
            email=None,
            audit_context=_audit_context(),
        )
    )

    assert result.patient.email is None
    assert result.changed_fields == (PatientMutableField.EMAIL,)
    assert repository.update_calls[0]["email"] is None

    assert isinstance(recorder, RecordingAuditRecorder)
    assert len(recorder.commands) == 1
    command = recorder.commands[0]
    assert command.metadata["changed_fields"] == ["email"]
    assert "old@example.com" not in str(command.metadata)
    _assert_no_pii_in_metadata(command.metadata)


def test_update_patient_rejects_empty_patch() -> None:
    current = _patient_record()
    repository = RecordingPatientRepository(reads=[current])
    service, _, recorder = _service(repository)

    with pytest.raises(PatientInvalidUpdateError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset(),
                audit_context=_audit_context(),
            )
        )

    assert repository.update_calls == []
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_rejects_clearing_full_name() -> None:
    current = _patient_record()
    repository = RecordingPatientRepository(reads=[current])
    service, _, recorder = _service(repository)

    with pytest.raises(PatientInvalidUpdateError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.FULL_NAME}),
                full_name=None,
                audit_context=_audit_context(),
            )
        )

    assert repository.update_calls == []
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_rejects_normalized_no_op() -> None:
    current = _patient_record(
        full_name="Jordan Lee",
        email="same@example.com",
    )
    repository = RecordingPatientRepository(reads=[current])
    service, _, recorder = _service(repository)

    with pytest.raises(PatientInvalidUpdateError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset(
                    {
                        PatientMutableField.FULL_NAME,
                        PatientMutableField.EMAIL,
                    }
                ),
                full_name="  Jordan Lee  ",
                email=" SAME@EXAMPLE.COM ",
                audit_context=_audit_context(),
            )
        )

    assert repository.update_calls == []
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_rejects_future_date_before_update() -> None:
    current = _patient_record()
    repository = RecordingPatientRepository(reads=[current])
    service, _, recorder = _service(repository)

    with pytest.raises(PatientInvalidDateOfBirthError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.DATE_OF_BIRTH}),
                date_of_birth=date.today() + timedelta(days=1),
                audit_context=_audit_context(),
            )
        )

    assert repository.update_calls == []
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_rejects_archived_patient() -> None:
    current = _patient_record(status=PatientStatus.ARCHIVED)
    repository = RecordingPatientRepository(reads=[current])
    service, _, recorder = _service(repository)

    with pytest.raises(PatientInvalidUpdateError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.EMAIL}),
                email="new@example.com",
                audit_context=_audit_context(),
            )
        )

    assert repository.update_calls == []
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_rejects_stale_version_before_update() -> None:
    current = _patient_record(version=4)
    repository = RecordingPatientRepository(reads=[current])
    service, _, recorder = _service(repository)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.EMAIL}),
                email="new@example.com",
                audit_context=_audit_context(),
            )
        )

    assert repository.update_calls == []
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_raises_not_found_for_invisible_patient() -> None:
    tenant_id = uuid4()
    patient_id = uuid4()
    repository = RecordingPatientRepository(reads=[None])
    service, _, recorder = _service(repository)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=1,
                fields_to_update=frozenset({PatientMutableField.EMAIL}),
                email="new@example.com",
                audit_context=_audit_context(),
            )
        )

    assert repository.update_calls == []
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_classifies_failed_atomic_update_as_version_conflict() -> None:
    current = _patient_record(version=3)
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        update_result=None,
    )
    service, _, recorder = _service(repository)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.EMAIL}),
                email="new@example.com",
                audit_context=_audit_context(),
            )
        )

    assert len(repository.update_calls) == 1
    assert len(repository.get_calls) == 2
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_classifies_failed_atomic_update_as_not_found() -> None:
    current = _patient_record(version=3)
    repository = RecordingPatientRepository(
        reads=[current, None],
        update_result=None,
    )
    service, _, recorder = _service(repository)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.EMAIL}),
                email="new@example.com",
                audit_context=_audit_context(),
            )
        )

    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_classifies_failed_atomic_update_as_invalid_lifecycle() -> None:
    current = _patient_record(version=3)
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=3,
        status=PatientStatus.ARCHIVED,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        update_result=None,
    )
    service, _, recorder = _service(repository)

    with pytest.raises(PatientInvalidUpdateError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.EMAIL}),
                email="new@example.com",
                audit_context=_audit_context(),
            )
        )

    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_propagates_external_reference_conflict() -> None:
    current = _patient_record()
    conflict = PatientExternalReferenceConflictError()
    repository = RecordingPatientRepository(
        reads=[current],
        update_error=conflict,
    )
    service, _, recorder = _service(repository)

    with pytest.raises(PatientExternalReferenceConflictError) as error:
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.EXTERNAL_REFERENCE}),
                external_reference="NEW-REFERENCE",
                audit_context=_audit_context(),
            )
        )

    assert error.value is conflict
    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.commands == []


def test_update_patient_propagates_audit_failure_after_successful_update() -> None:
    current = _patient_record()
    updated = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
        email="new@example.com",
    )
    repository = RecordingPatientRepository(
        reads=[current],
        update_result=updated,
    )
    service, _, recorder = _service(
        repository,
        audit_recorder=FailingAuditRecorder(),
    )

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(
            UpdatePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                fields_to_update=frozenset({PatientMutableField.EMAIL}),
                email="new@example.com",
                audit_context=_audit_context(),
            )
        )

    assert len(repository.update_calls) == 1
    assert isinstance(recorder, FailingAuditRecorder)
