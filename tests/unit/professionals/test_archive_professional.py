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
from clinicops.professionals.contracts import (
    ArchiveProfessionalCommand,
    ProfessionalRecord,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalAlreadyArchivedError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.archive_professional import (
    ArchiveProfessionalService,
)

_RECORDED_AT = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)
_AUDIT_RECORDED_AT = datetime(2026, 7, 24, 21, 1, tzinfo=UTC)
_FORBIDDEN_METADATA_KEYS = {
    "full_name",
    "specialty",
    "registration_number",
    "registration_region",
    "email",
    "phone",
    "external_reference",
    "tenant_id",
}


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class RecordingProfessionalRepository:
    """Record optimistic professional archive interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        archived: ProfessionalRecord | None = None,
        archive_error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._archived = archived
        self._archive_error = archive_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.archive_calls: list[tuple[UUID, UUID, int]] = []
        self._events = events

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def archive_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        self.archive_calls.append((tenant_id, professional_id, expected_version))
        if self._events is not None:
            self._events.append("archive")
        if self._archive_error is not None:
            raise self._archive_error
        return self._archived


class RecordingAuditRecorder:
    def __init__(
        self,
        events: list[str] | None = None,
    ) -> None:
        self.sessions: list[Session] = []
        self.commands: list[RecordAuditLogCommand] = []
        self._events = events

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        self.sessions.append(session)
        self.commands.append(command)
        if self._events is not None:
            self._events.append("audit")

        return RecordedAuditLog(
            audit_log_id=uuid4(),
            created=True,
            recorded_at=_AUDIT_RECORDED_AT,
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
        role="admin",
        request_id="request-professional-archive",
        correlation_id="correlation-professional-archive",
    )


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _service(
    repository: RecordingProfessionalRepository,
    session: Session,
    recorder: RecordingAuditRecorder | FailingAuditRecorder,
) -> ArchiveProfessionalService:
    return ArchiveProfessionalService(
        _repository(repository),
        session,
        cast(AuditRecorder, recorder),
    )


def _professional_record(
    *,
    tenant_id: UUID | None = None,
    professional_id: UUID | None = None,
    membership_id: UUID | None = None,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    version: int = 1,
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=professional_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        membership_id=membership_id,
        full_name="Morgan Reed",
        specialty="Dentistry",
        registration_number="DDS-48291",
        registration_region="CA",
        email="morgan@example.com",
        phone="+1-202-555-0130",
        external_reference="PROVIDER-100",
        status=status,
        version=version,
        created_at=_RECORDED_AT,
        updated_at=_RECORDED_AT,
    )


def _command(
    current: ProfessionalRecord,
    *,
    expected_version: int | None = None,
    audit_context: AuditRecordingContext | None = None,
) -> ArchiveProfessionalCommand:
    return ArchiveProfessionalCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        expected_version=(current.version if expected_version is None else expected_version),
        audit_context=audit_context or _audit_context(),
    )


def test_archive_professional_returns_archived_versioned_record() -> None:
    membership_id = uuid4()
    current = _professional_record(membership_id=membership_id)
    archived = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id,
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        archived=archived,
        events=events,
    )
    session = cast(Session, object())
    audit_context = _audit_context()
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)

    result = service.execute(_command(current, audit_context=audit_context))

    assert result.professional is archived
    assert result.professional.status is ProfessionalStatus.ARCHIVED
    assert result.professional.version == 2
    assert result.professional.membership_id == membership_id
    assert recording.get_calls == [(current.tenant_id, current.id)]
    assert recording.archive_calls == [(current.tenant_id, current.id, 1)]
    assert events == ["archive", "audit"]

    assert len(recorder.commands) == 1
    audit_command = recorder.commands[0]
    assert audit_command.tenant_id == current.tenant_id
    assert audit_command.action == AuditAction.PROFESSIONAL_ARCHIVED.value
    assert audit_command.action == "professional.archived"
    assert audit_command.resource_type == AuditResourceType.PROFESSIONAL.value
    assert audit_command.resource_id == str(archived.id)
    assert audit_command.actor == audit_context.actor
    assert audit_command.source is AuditSource.HTTP
    assert audit_command.request_id == audit_context.request_id
    assert audit_command.correlation_id == audit_context.correlation_id
    assert audit_command.metadata == {
        "previous_status": "active",
        "new_status": "archived",
        "version": 2,
    }
    assert audit_command.idempotency_key == (f"professional-archived:{archived.id}:2")
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(audit_command.metadata)
    assert recorder.sessions == [session]


def test_archive_professional_raises_not_found_for_invisible_record() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            ArchiveProfessionalCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                expected_version=1,
                audit_context=_audit_context(),
            )
        )

    assert recording.get_calls == [(tenant_id, professional_id)]
    assert recording.archive_calls == []
    assert recorder.commands == []


def test_archive_professional_rejects_stale_version_before_mutation() -> None:
    current = _professional_record(version=4)
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, expected_version=3))

    assert recording.archive_calls == []
    assert recorder.commands == []


def test_archive_professional_rejects_already_archived_record() -> None:
    current = _professional_record(
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalAlreadyArchivedError):
        service.execute(_command(current))

    assert recording.archive_calls == []
    assert recorder.commands == []


def test_archive_professional_classifies_concurrent_archive_as_version_conflict() -> None:
    current = _professional_record(version=1)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        archived=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current))

    assert recording.get_calls == [
        (current.tenant_id, current.id),
        (current.tenant_id, current.id),
    ]
    assert recording.archive_calls == [(current.tenant_id, current.id, 1)]
    assert recorder.commands == []


def test_archive_professional_classifies_concurrent_removal_as_not_found() -> None:
    current = _professional_record(version=1)
    recording = RecordingProfessionalRepository(
        reads=[current, None],
        archived=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(_command(current))

    assert len(recording.archive_calls) == 1
    assert recorder.commands == []


def test_archive_professional_propagates_repository_failure() -> None:
    current = _professional_record()
    error = RuntimeError("database unavailable")
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        archive_error=error,
        events=events,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)

    with pytest.raises(RuntimeError) as captured:
        service.execute(_command(current))

    assert captured.value is error
    assert len(recording.archive_calls) == 1
    assert events == ["archive"]
    assert recorder.commands == []


def test_archive_professional_propagates_audit_failure_after_archive() -> None:
    current = _professional_record()
    archived = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        archived=archived,
        events=events,
    )
    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(_command(current))

    assert len(recording.archive_calls) == 1
    assert events == ["archive"]
