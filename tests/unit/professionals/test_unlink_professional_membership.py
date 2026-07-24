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
    ProfessionalRecord,
    UnlinkProfessionalMembershipCommand,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalAlreadyArchivedError,
    ProfessionalNotFoundError,
    ProfessionalNotLinkedError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.unlink_professional_membership import (
    UnlinkProfessionalMembershipService,
)

_RECORDED_AT = datetime(2026, 7, 24, 22, 30, tzinfo=UTC)
_AUDIT_RECORDED_AT = datetime(2026, 7, 24, 22, 31, tzinfo=UTC)
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
    """Record explicit professional-membership unlink interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        unlinked: ProfessionalRecord | None = None,
        unlink_error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._unlinked = unlinked
        self._unlink_error = unlink_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.unlink_calls: list[tuple[UUID, UUID, int]] = []
        self._events = events

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def unlink_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        self.unlink_calls.append((tenant_id, professional_id, expected_version))
        if self._events is not None:
            self._events.append("unlink")
        if self._unlink_error is not None:
            raise self._unlink_error
        return self._unlinked


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
        request_id="request-professional-unlink-membership",
        correlation_id="correlation-professional-unlink-membership",
    )


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _service(
    repository: RecordingProfessionalRepository,
    session: Session,
    recorder: RecordingAuditRecorder | FailingAuditRecorder,
) -> UnlinkProfessionalMembershipService:
    return UnlinkProfessionalMembershipService(
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
) -> UnlinkProfessionalMembershipCommand:
    return UnlinkProfessionalMembershipCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        expected_version=(current.version if expected_version is None else expected_version),
        audit_context=audit_context or _audit_context(),
    )


def test_unlink_professional_membership_returns_previous_membership() -> None:
    membership_id = uuid4()
    current = _professional_record(membership_id=membership_id)
    unlinked = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=None,
        version=2,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        unlinked=unlinked,
        events=events,
    )
    session = cast(Session, object())
    audit_context = _audit_context()
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)

    result = service.execute(_command(current, audit_context=audit_context))

    assert result.professional is unlinked
    assert result.professional.membership_id is None
    assert result.professional.version == 2
    assert result.previous_membership_id == membership_id
    assert recording.unlink_calls == [(current.tenant_id, current.id, 1)]
    assert events == ["unlink", "audit"]

    assert len(recorder.commands) == 1
    audit_command = recorder.commands[0]
    assert audit_command.tenant_id == current.tenant_id
    assert audit_command.action == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
    assert audit_command.action == "professional.membership_unlinked"
    assert audit_command.resource_type == AuditResourceType.PROFESSIONAL.value
    assert audit_command.resource_id == str(unlinked.id)
    assert audit_command.actor == audit_context.actor
    assert audit_command.source is AuditSource.HTTP
    assert audit_command.request_id == audit_context.request_id
    assert audit_command.correlation_id == audit_context.correlation_id
    assert audit_command.metadata == {
        "membership_id": str(membership_id),
        "reason": "explicit",
        "version": unlinked.version,
    }
    assert audit_command.idempotency_key == (f"professional-membership-unlinked:{unlinked.id}:2")
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(audit_command.metadata)
    assert recorder.sessions == [session]


def test_unlink_professional_membership_raises_not_found() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            UnlinkProfessionalMembershipCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                expected_version=1,
                audit_context=_audit_context(),
            )
        )

    assert recording.unlink_calls == []
    assert recorder.commands == []


def test_unlink_professional_membership_rejects_stale_version() -> None:
    current = _professional_record(
        membership_id=uuid4(),
        version=4,
    )
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, expected_version=3))

    assert recording.unlink_calls == []
    assert recorder.commands == []


def test_unlink_professional_membership_rejects_archived_professional() -> None:
    current = _professional_record(
        membership_id=uuid4(),
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalAlreadyArchivedError):
        service.execute(_command(current))

    assert recording.unlink_calls == []
    assert recorder.commands == []


def test_unlink_professional_membership_rejects_unlinked_professional() -> None:
    current = _professional_record(membership_id=None)
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalNotLinkedError):
        service.execute(_command(current))

    assert recording.unlink_calls == []
    assert recorder.commands == []


def test_unlink_professional_membership_classifies_concurrent_winner() -> None:
    membership_id = uuid4()
    current = _professional_record(
        membership_id=membership_id,
        version=1,
    )
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=None,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        unlinked=None,
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
    assert len(recording.unlink_calls) == 1
    assert recorder.commands == []


def test_unlink_professional_membership_propagates_repository_failure() -> None:
    current = _professional_record(membership_id=uuid4())
    error = RuntimeError("database unavailable")
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        unlink_error=error,
        events=events,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)

    with pytest.raises(RuntimeError) as captured:
        service.execute(_command(current))

    assert captured.value is error
    assert len(recording.unlink_calls) == 1
    assert events == ["unlink"]
    assert recorder.commands == []


def test_unlink_professional_membership_propagates_audit_failure_after_unlink() -> None:
    membership_id = uuid4()
    current = _professional_record(membership_id=membership_id)
    unlinked = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=None,
        version=2,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        unlinked=unlinked,
        events=events,
    )
    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(_command(current))

    assert len(recording.unlink_calls) == 1
    assert events == ["unlink"]
