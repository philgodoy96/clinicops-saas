from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand, RecordedAuditLog
from clinicops.audit.enums import AuditSource
from clinicops.audit.recording import AuditRecorder
from clinicops.patients.contracts import (
    PatientRecord,
    RestorePatientCommand,
)
from clinicops.patients.enums import PatientStatus
from clinicops.patients.exceptions import (
    PatientNotArchivedError,
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.restore_patient import (
    RestorePatientService,
)

_RECORDED_AT = datetime(2026, 7, 23, 21, 31, tzinfo=UTC)


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class RecordingPatientRepository:
    def __init__(
        self,
        *,
        reads: list[PatientRecord | None],
        restore_result: PatientRecord | None = None,
    ) -> None:
        self.reads = list(reads)
        self.restore_result = restore_result
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.restore_calls: list[tuple[UUID, UUID, int]] = []

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

    def restore_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
        expected_version: int,
    ) -> PatientRecord | None:
        self.restore_calls.append((tenant_id, patient_id, expected_version))
        return self.restore_result


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
            recorded_at=_RECORDED_AT,
        )


class FailingAuditRecorder:
    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError("audit recording failed")


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=uuid4(),
        role="owner",
        request_id="request-patient-restore",
        correlation_id="correlation-patient-restore",
    )


def _patient_record(
    *,
    tenant_id: UUID | None = None,
    patient_id: UUID | None = None,
    status: PatientStatus = PatientStatus.ARCHIVED,
    version: int = 3,
) -> PatientRecord:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    return PatientRecord(
        id=patient_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        full_name="Jordan Lee",
        date_of_birth=None,
        email=None,
        phone=None,
        external_reference=None,
        status=status,
        version=version,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _service(
    repository: RecordingPatientRepository,
    session: Session,
    recorder: RecordingAuditRecorder | FailingAuditRecorder,
) -> RestorePatientService:
    return RestorePatientService(
        cast(PatientRepository, repository),
        session,
        cast(AuditRecorder, recorder),
    )


def test_restore_patient_returns_restored_record() -> None:
    current = _patient_record()
    restored = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ACTIVE,
        version=4,
    )
    repository = RecordingPatientRepository(
        reads=[current],
        restore_result=restored,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    result = service.execute(
        RestorePatientCommand(
            tenant_id=current.tenant_id,
            patient_id=current.id,
            expected_version=3,
            audit_context=_audit_context(),
        )
    )

    assert result.patient is restored
    assert repository.restore_calls == [(current.tenant_id, current.id, 3)]

    assert len(recorder.commands) == 1
    command = recorder.commands[0]
    assert command.action == AuditAction.PATIENT_RESTORED.value
    assert command.resource_type == AuditResourceType.PATIENT.value
    assert command.resource_id == str(restored.id)
    assert command.source is AuditSource.HTTP
    assert command.request_id == "request-patient-restore"
    assert command.correlation_id == "correlation-patient-restore"
    assert command.metadata == {
        "previous_status": "archived",
        "new_status": "active",
        "version": 4,
    }
    assert command.idempotency_key == f"patient-restored:{restored.id}:4"
    assert recorder.sessions == [session]
    assert "full_name" not in command.metadata
    assert "email" not in command.metadata
    assert "phone" not in command.metadata
    assert "date_of_birth" not in command.metadata
    assert "external_reference" not in command.metadata


def test_restore_patient_raises_not_found_for_invisible_patient() -> None:
    tenant_id = uuid4()
    patient_id = uuid4()
    repository = RecordingPatientRepository(reads=[None])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            RestorePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=1,
                audit_context=_audit_context(),
            )
        )

    assert repository.restore_calls == []
    assert recorder.commands == []


def test_restore_patient_rejects_stale_version_before_transition() -> None:
    current = _patient_record(version=4)
    repository = RecordingPatientRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert repository.restore_calls == []
    assert recorder.commands == []


def test_restore_patient_rejects_active_patient() -> None:
    current = _patient_record(status=PatientStatus.ACTIVE)
    repository = RecordingPatientRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientNotArchivedError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert repository.restore_calls == []
    assert recorder.commands == []


def test_restore_patient_classifies_failed_transition_as_version_conflict() -> None:
    current = _patient_record(version=3)
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
        status=PatientStatus.ACTIVE,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        restore_result=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert len(repository.get_calls) == 2
    assert len(repository.restore_calls) == 1
    assert recorder.commands == []


def test_restore_patient_classifies_failed_transition_as_not_found() -> None:
    current = _patient_record()
    repository = RecordingPatientRepository(
        reads=[current, None],
        restore_result=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert recorder.commands == []


def test_restore_patient_classifies_same_version_active_state() -> None:
    current = _patient_record()
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ACTIVE,
        version=3,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        restore_result=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientNotArchivedError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert recorder.commands == []


def test_restore_patient_propagates_audit_failure_after_transition() -> None:
    current = _patient_record()
    restored = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ACTIVE,
        version=4,
    )
    repository = RecordingPatientRepository(
        reads=[current],
        restore_result=restored,
    )
    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert repository.restore_calls == [(current.tenant_id, current.id, 3)]
