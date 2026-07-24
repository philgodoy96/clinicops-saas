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
    ArchivePatientCommand,
    PatientRecord,
)
from clinicops.patients.enums import PatientStatus
from clinicops.patients.exceptions import (
    PatientAlreadyArchivedError,
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.archive_patient import (
    ArchivePatientService,
)

_RECORDED_AT = datetime(2026, 7, 23, 21, 31, tzinfo=UTC)


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class RecordingPatientRepository:
    def __init__(
        self,
        *,
        reads: list[PatientRecord | None],
        archive_result: PatientRecord | None = None,
    ) -> None:
        self.reads = list(reads)
        self.archive_result = archive_result
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.archive_calls: list[tuple[UUID, UUID, int]] = []

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

    def archive_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
        expected_version: int,
    ) -> PatientRecord | None:
        self.archive_calls.append((tenant_id, patient_id, expected_version))
        return self.archive_result


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
        request_id="request-patient-archive",
        correlation_id="correlation-patient-archive",
    )


def _patient_record(
    *,
    tenant_id: UUID | None = None,
    patient_id: UUID | None = None,
    status: PatientStatus = PatientStatus.ACTIVE,
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
) -> ArchivePatientService:
    return ArchivePatientService(
        cast(PatientRepository, repository),
        session,
        cast(AuditRecorder, recorder),
    )


def test_archive_patient_returns_archived_record() -> None:
    current = _patient_record()
    archived = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ARCHIVED,
        version=4,
    )
    repository = RecordingPatientRepository(
        reads=[current],
        archive_result=archived,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    result = service.execute(
        ArchivePatientCommand(
            tenant_id=current.tenant_id,
            patient_id=current.id,
            expected_version=3,
            audit_context=_audit_context(),
        )
    )

    assert result.patient is archived
    assert repository.archive_calls == [(current.tenant_id, current.id, 3)]

    assert len(recorder.commands) == 1
    command = recorder.commands[0]
    assert command.action == AuditAction.PATIENT_ARCHIVED.value
    assert command.resource_type == AuditResourceType.PATIENT.value
    assert command.resource_id == str(archived.id)
    assert command.source is AuditSource.HTTP
    assert command.request_id == "request-patient-archive"
    assert command.correlation_id == "correlation-patient-archive"
    assert command.metadata == {
        "previous_status": "active",
        "new_status": "archived",
        "version": 4,
    }
    assert command.idempotency_key == f"patient-archived:{archived.id}:4"
    assert recorder.sessions == [session]
    assert "full_name" not in command.metadata
    assert "email" not in command.metadata
    assert "phone" not in command.metadata
    assert "date_of_birth" not in command.metadata
    assert "external_reference" not in command.metadata


def test_archive_patient_raises_not_found_for_invisible_patient() -> None:
    tenant_id = uuid4()
    patient_id = uuid4()
    repository = RecordingPatientRepository(reads=[None])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=1,
                audit_context=_audit_context(),
            )
        )

    assert repository.archive_calls == []
    assert recorder.commands == []


def test_archive_patient_rejects_stale_version_before_transition() -> None:
    current = _patient_record(version=4)
    repository = RecordingPatientRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert repository.archive_calls == []
    assert recorder.commands == []


def test_archive_patient_rejects_already_archived_patient() -> None:
    current = _patient_record(
        status=PatientStatus.ARCHIVED,
    )
    repository = RecordingPatientRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientAlreadyArchivedError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert repository.archive_calls == []
    assert recorder.commands == []


def test_archive_patient_classifies_failed_transition_as_version_conflict() -> None:
    current = _patient_record(version=3)
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
        status=PatientStatus.ARCHIVED,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        archive_result=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert len(repository.get_calls) == 2
    assert len(repository.archive_calls) == 1
    assert recorder.commands == []


def test_archive_patient_classifies_failed_transition_as_not_found() -> None:
    current = _patient_record()
    repository = RecordingPatientRepository(
        reads=[current, None],
        archive_result=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert recorder.commands == []


def test_archive_patient_classifies_same_version_archived_state() -> None:
    current = _patient_record()
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ARCHIVED,
        version=3,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        archive_result=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(PatientAlreadyArchivedError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert recorder.commands == []


def test_archive_patient_propagates_audit_failure_after_transition() -> None:
    current = _patient_record()
    archived = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ARCHIVED,
        version=4,
    )
    repository = RecordingPatientRepository(
        reads=[current],
        archive_result=archived,
    )
    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
                audit_context=_audit_context(),
            )
        )

    assert repository.archive_calls == [(current.tenant_id, current.id, 3)]
