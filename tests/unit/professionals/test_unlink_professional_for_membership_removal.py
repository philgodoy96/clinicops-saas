from collections.abc import Callable
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
    UnlinkProfessionalForMembershipRemovalCommand,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.unlink_professional_for_membership_removal import (
    UnlinkProfessionalForMembershipRemovalService,
)

_RECORDED_AT = datetime(2026, 7, 24, 23, 0, tzinfo=UTC)
_AUDIT_RECORDED_AT = datetime(2026, 7, 24, 23, 1, tzinfo=UTC)
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
    """Record Membership-removal unlink interactions."""

    def __init__(
        self,
        *,
        unlinked: ProfessionalRecord | None,
        unlink_error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._unlinked = unlinked
        self._unlink_error = unlink_error
        self.calls: list[tuple[UUID, UUID]] = []
        self._events = events

    def unlink_by_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> ProfessionalRecord | None:
        self.calls.append((tenant_id, membership_id))
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
        request_id="request-professional-unlink-membership-removal",
        correlation_id="correlation-professional-unlink-membership-removal",
    )


def _professional_record(
    *,
    tenant_id: UUID,
    membership_id: UUID | None,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    version: int = 2,
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=uuid4(),
        tenant_id=tenant_id,
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


def _service(
    *,
    repository_factory: Callable[[Session], ProfessionalRepository],
    recorder: RecordingAuditRecorder | FailingAuditRecorder,
) -> UnlinkProfessionalForMembershipRemovalService:
    return UnlinkProfessionalForMembershipRemovalService(
        repository_factory,
        cast(AuditRecorder, recorder),
    )


def _command(
    *,
    tenant_id: UUID,
    membership_id: UUID,
    audit_context: AuditRecordingContext | None = None,
) -> UnlinkProfessionalForMembershipRemovalCommand:
    return UnlinkProfessionalForMembershipRemovalCommand(
        tenant_id=tenant_id,
        membership_id=membership_id,
        audit_context=audit_context or _audit_context(),
    )


def test_membership_removal_unlinks_professional_in_caller_session() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    unlinked = _professional_record(
        tenant_id=tenant_id,
        membership_id=None,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(unlinked=unlinked, events=events)
    received_sessions: list[Session] = []

    def repository_factory(session: Session) -> ProfessionalRepository:
        received_sessions.append(session)
        return cast(ProfessionalRepository, recording)

    session = cast(Session, object())
    audit_context = _audit_context()
    recorder = RecordingAuditRecorder(events=events)
    service = _service(repository_factory=repository_factory, recorder=recorder)

    result = service.execute(
        session,
        _command(
            tenant_id=tenant_id,
            membership_id=membership_id,
            audit_context=audit_context,
        ),
    )

    assert result.professional is unlinked
    assert result.previous_membership_id == membership_id
    assert received_sessions == [session]
    assert recording.calls == [(tenant_id, membership_id)]
    assert events == ["unlink", "audit"]

    assert len(recorder.commands) == 1
    audit_command = recorder.commands[0]
    assert audit_command.tenant_id == tenant_id
    assert audit_command.action == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
    assert audit_command.action == "professional.membership_unlinked"
    assert audit_command.resource_type == AuditResourceType.PROFESSIONAL.value
    assert audit_command.resource_type == "professional"
    assert audit_command.resource_id == str(unlinked.id)
    assert audit_command.actor == audit_context.actor
    assert audit_command.source is AuditSource.HTTP
    assert audit_command.request_id == audit_context.request_id
    assert audit_command.correlation_id == audit_context.correlation_id
    assert audit_command.metadata == {
        "membership_id": str(membership_id),
        "reason": "membership_removal",
        "version": unlinked.version,
    }
    assert audit_command.idempotency_key == (
        f"professional-membership-unlinked:{unlinked.id}:{unlinked.version}"
    )
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(audit_command.metadata)
    assert recorder.sessions == [session]


def test_membership_removal_preserves_archived_professional_unlink_result() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    unlinked = _professional_record(
        tenant_id=tenant_id,
        membership_id=None,
        status=ProfessionalStatus.ARCHIVED,
        version=5,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(unlinked=unlinked, events=events)
    received_sessions: list[Session] = []

    def repository_factory(session: Session) -> ProfessionalRepository:
        received_sessions.append(session)
        return cast(ProfessionalRepository, recording)

    session = cast(Session, object())
    audit_context = _audit_context()
    recorder = RecordingAuditRecorder(events=events)
    service = _service(repository_factory=repository_factory, recorder=recorder)

    result = service.execute(
        session,
        _command(
            tenant_id=tenant_id,
            membership_id=membership_id,
            audit_context=audit_context,
        ),
    )

    assert result.professional is unlinked
    assert result.professional.status is ProfessionalStatus.ARCHIVED
    assert result.professional.version == 5
    assert result.previous_membership_id == membership_id
    assert received_sessions == [session]
    assert recording.calls == [(tenant_id, membership_id)]
    assert events == ["unlink", "audit"]

    assert len(recorder.commands) == 1
    audit_command = recorder.commands[0]
    assert audit_command.action == "professional.membership_unlinked"
    assert audit_command.resource_type == "professional"
    assert audit_command.resource_id == str(unlinked.id)
    assert audit_command.actor == audit_context.actor
    assert audit_command.source is AuditSource.HTTP
    assert audit_command.request_id == audit_context.request_id
    assert audit_command.correlation_id == audit_context.correlation_id
    assert audit_command.metadata == {
        "membership_id": str(membership_id),
        "reason": "membership_removal",
        "version": 5,
    }
    assert "status" not in audit_command.metadata
    assert audit_command.idempotency_key == (f"professional-membership-unlinked:{unlinked.id}:5")
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(audit_command.metadata)
    assert recorder.sessions == [session]


def test_membership_removal_is_no_op_when_no_professional_is_linked() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    events: list[str] = []
    recording = RecordingProfessionalRepository(unlinked=None, events=events)
    received_sessions: list[Session] = []

    def repository_factory(session: Session) -> ProfessionalRepository:
        received_sessions.append(session)
        return cast(ProfessionalRepository, recording)

    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=events)
    service = _service(repository_factory=repository_factory, recorder=recorder)

    result = service.execute(
        session,
        _command(tenant_id=tenant_id, membership_id=membership_id),
    )

    assert result.professional is None
    assert result.previous_membership_id is None
    assert received_sessions == [session]
    assert recording.calls == [(tenant_id, membership_id)]
    assert events == ["unlink"]
    assert recorder.commands == []
    assert recorder.sessions == []


def test_membership_removal_propagates_repository_failure() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    error = RuntimeError("database unavailable")
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        unlinked=None,
        unlink_error=error,
        events=events,
    )
    received_sessions: list[Session] = []

    def repository_factory(session: Session) -> ProfessionalRepository:
        received_sessions.append(session)
        return cast(ProfessionalRepository, recording)

    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=events)
    service = _service(repository_factory=repository_factory, recorder=recorder)

    with pytest.raises(RuntimeError) as captured:
        service.execute(
            session,
            _command(tenant_id=tenant_id, membership_id=membership_id),
        )

    assert captured.value is error
    assert received_sessions == [session]
    assert recording.calls == [(tenant_id, membership_id)]
    assert events == ["unlink"]
    assert recorder.commands == []


def test_membership_removal_propagates_audit_failure_after_unlink() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    unlinked = _professional_record(
        tenant_id=tenant_id,
        membership_id=None,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(unlinked=unlinked, events=events)
    received_sessions: list[Session] = []

    def repository_factory(session: Session) -> ProfessionalRepository:
        received_sessions.append(session)
        return cast(ProfessionalRepository, recording)

    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(repository_factory=repository_factory, recorder=recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(
            session,
            _command(tenant_id=tenant_id, membership_id=membership_id),
        )

    assert received_sessions == [session]
    assert recording.calls == [(tenant_id, membership_id)]
    assert events == ["unlink"]
