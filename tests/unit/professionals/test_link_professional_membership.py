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
    LinkProfessionalMembershipCommand,
    ProfessionalRecord,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalAlreadyArchivedError,
    ProfessionalAlreadyLinkedError,
    ProfessionalMembershipInactiveError,
    ProfessionalMembershipLinkConflictError,
    ProfessionalMembershipNotFoundError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.link_professional_membership import (
    LinkProfessionalMembershipService,
)
from clinicops.tenancy.models import MembershipStatus

_RECORDED_AT = datetime(2026, 7, 24, 22, 0, tzinfo=UTC)
_AUDIT_RECORDED_AT = datetime(2026, 7, 24, 22, 1, tzinfo=UTC)
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
    """Record explicit professional-membership link interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        membership_status: MembershipStatus | None = MembershipStatus.ACTIVE,
        linked: ProfessionalRecord | None = None,
        link_error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._membership_status = membership_status
        self._linked = linked
        self._link_error = link_error
        self._events = events
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.membership_calls: list[tuple[UUID, UUID]] = []
        self.link_calls: list[tuple[UUID, UUID, UUID, int]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def get_membership_status_for_tenant_for_update(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> MembershipStatus | None:
        self.membership_calls.append((tenant_id, membership_id))
        return self._membership_status

    def link_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        membership_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        self.link_calls.append(
            (
                tenant_id,
                professional_id,
                membership_id,
                expected_version,
            )
        )
        if self._events is not None:
            self._events.append("link")
        if self._link_error is not None:
            raise self._link_error
        return self._linked


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
        request_id="request-professional-membership-link",
        correlation_id="correlation-professional-membership-link",
    )


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _service(
    repository: RecordingProfessionalRepository,
    session: Session,
    recorder: RecordingAuditRecorder | FailingAuditRecorder,
) -> LinkProfessionalMembershipService:
    return LinkProfessionalMembershipService(
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
    membership_id: UUID | None = None,
    expected_version: int | None = None,
    audit_context: AuditRecordingContext | None = None,
) -> LinkProfessionalMembershipCommand:
    return LinkProfessionalMembershipCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id or uuid4(),
        expected_version=(current.version if expected_version is None else expected_version),
        audit_context=audit_context or _audit_context(),
    )


def test_link_professional_membership_returns_versioned_link() -> None:
    membership_id = uuid4()
    current = _professional_record()
    linked = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id,
        version=2,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        linked=linked,
        events=events,
    )
    session = cast(Session, object())
    audit_context = _audit_context()
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)
    command = _command(
        current,
        membership_id=membership_id,
        audit_context=audit_context,
    )

    result = service.execute(command)

    assert result.professional is linked
    assert result.professional.membership_id == membership_id
    assert result.professional.version == 2
    assert recording.get_calls == [(current.tenant_id, current.id)]
    assert recording.membership_calls == [(current.tenant_id, membership_id)]
    assert recording.link_calls == [
        (
            current.tenant_id,
            current.id,
            membership_id,
            1,
        )
    ]
    assert events == ["link", "audit"]

    assert len(recorder.commands) == 1
    audit_command = recorder.commands[0]
    assert audit_command.action == AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value
    assert audit_command.action == "professional.membership_linked"
    assert audit_command.resource_type == AuditResourceType.PROFESSIONAL.value
    assert audit_command.resource_id == str(linked.id)
    assert audit_command.metadata == {
        "membership_id": str(membership_id),
        "version": linked.version,
    }
    assert audit_command.idempotency_key == (
        f"professional-membership-linked:{linked.id}:{linked.version}"
    )
    assert audit_command.actor == audit_context.actor
    assert audit_command.source is AuditSource.HTTP
    assert audit_command.request_id == audit_context.request_id
    assert audit_command.correlation_id == audit_context.correlation_id
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(audit_command.metadata)
    assert recorder.sessions == [session]


def test_link_professional_membership_raises_not_found_for_professional() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            LinkProfessionalMembershipCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                membership_id=uuid4(),
                expected_version=1,
                audit_context=_audit_context(),
            )
        )

    assert recording.membership_calls == []
    assert recording.link_calls == []
    assert recorder.commands == []


def test_link_professional_membership_rejects_stale_version() -> None:
    current = _professional_record(version=4)
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, expected_version=3))

    assert recording.membership_calls == []
    assert recording.link_calls == []
    assert recorder.commands == []


def test_link_professional_membership_rejects_archived_professional() -> None:
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

    assert recording.membership_calls == []
    assert recording.link_calls == []
    assert recorder.commands == []


def test_link_professional_membership_rejects_already_linked_professional() -> None:
    current = _professional_record(membership_id=uuid4())
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalAlreadyLinkedError):
        service.execute(_command(current))

    assert recording.membership_calls == []
    assert recording.link_calls == []
    assert recorder.commands == []


def test_link_professional_membership_hides_foreign_or_missing_membership() -> None:
    current = _professional_record()
    membership_id = uuid4()
    recording = RecordingProfessionalRepository(
        reads=[current],
        membership_status=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalMembershipNotFoundError):
        service.execute(_command(current, membership_id=membership_id))

    assert recording.membership_calls == [(current.tenant_id, membership_id)]
    assert recording.link_calls == []
    assert recorder.commands == []


def test_link_professional_membership_rejects_inactive_membership() -> None:
    current = _professional_record()
    membership_id = uuid4()
    recording = RecordingProfessionalRepository(
        reads=[current],
        membership_status=MembershipStatus.DISABLED,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalMembershipInactiveError):
        service.execute(_command(current, membership_id=membership_id))

    assert recording.link_calls == []
    assert recorder.commands == []


def test_link_professional_membership_propagates_membership_conflict() -> None:
    current = _professional_record()
    error = ProfessionalMembershipLinkConflictError()
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        link_error=error,
        events=events,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalMembershipLinkConflictError) as captured:
        service.execute(_command(current))

    assert captured.value is error
    assert len(recording.link_calls) == 1
    assert events == ["link"]
    assert recorder.commands == []


def test_link_professional_membership_classifies_concurrent_winner() -> None:
    membership_id = uuid4()
    current = _professional_record(version=1)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id,
        version=2,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        linked=None,
        events=events,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, membership_id=membership_id))

    assert recording.get_calls == [
        (current.tenant_id, current.id),
        (current.tenant_id, current.id),
    ]
    assert len(recording.link_calls) == 1
    assert events == ["link"]
    assert recorder.commands == []


def test_link_professional_membership_propagates_audit_failure_after_link() -> None:
    membership_id = uuid4()
    current = _professional_record()
    linked = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id,
        version=2,
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        linked=linked,
        events=events,
    )
    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(_command(current, membership_id=membership_id))

    assert len(recording.link_calls) == 1
    assert events == ["link"]
