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
    CreatedProfessional,
    CreateProfessionalCommand,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalExternalReferenceConflictError,
)
from clinicops.professionals.models import Professional
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.create_professional import (
    CreateProfessionalService,
)

_PERSISTED_AT = datetime(2026, 7, 24, 18, 0, tzinfo=UTC)
_RECORDED_AT = datetime(2026, 7, 24, 18, 1, tzinfo=UTC)
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
    """Record creation persistence interactions without a database."""

    def __init__(
        self,
        *,
        flush_error: Exception | None = None,
    ) -> None:
        self.added_professional: Professional | None = None
        self.events: list[str] = []
        self.flush_count = 0
        self._flush_error = flush_error

    def add(self, professional: Professional) -> None:
        self.events.append("add")
        self.added_professional = professional

    def flush(self) -> None:
        self.events.append("flush")
        self.flush_count += 1

        if self._flush_error is not None:
            raise self._flush_error

        professional = self.added_professional
        if professional is None:
            raise AssertionError("flush called before add")

        professional.id = uuid4()
        professional.status = ProfessionalStatus.ACTIVE
        professional.version = 1
        professional.created_at = _PERSISTED_AT
        professional.updated_at = _PERSISTED_AT


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
        role="admin",
        request_id="request-professional-create",
        correlation_id="correlation-professional-create",
    )


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _service(
    repository: RecordingProfessionalRepository,
    session: Session,
    recorder: RecordingAuditRecorder | FailingAuditRecorder,
) -> CreateProfessionalService:
    return CreateProfessionalService(
        _repository(repository),
        session,
        cast(AuditRecorder, recorder),
    )


def _command(
    *,
    tenant_id: UUID | None = None,
    audit_context: AuditRecordingContext | None = None,
    full_name: str = "Morgan Reed",
    specialty: str | None = "Dentistry",
    registration_number: str | None = "DDS-48291",
    registration_region: str | None = "CA",
    email: str | None = "morgan@example.com",
    phone: str | None = "+1-202-555-0130",
    external_reference: str | None = "PROVIDER-100",
) -> CreateProfessionalCommand:
    return CreateProfessionalCommand(
        tenant_id=tenant_id or uuid4(),
        full_name=full_name,
        audit_context=audit_context or _audit_context(),
        specialty=specialty,
        registration_number=registration_number,
        registration_region=registration_region,
        email=email,
        phone=phone,
        external_reference=external_reference,
    )


def test_create_professional_persists_and_returns_domain_result() -> None:
    tenant_id = uuid4()
    audit_context = _audit_context()
    recording = RecordingProfessionalRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=recording.events)
    service = _service(recording, session, recorder)
    command = _command(tenant_id=tenant_id, audit_context=audit_context)

    result = service.execute(command)

    assert isinstance(result, CreatedProfessional)
    assert result.professional.tenant_id == command.tenant_id
    assert result.professional.membership_id is None
    assert result.professional.full_name == "Morgan Reed"
    assert result.professional.specialty == "Dentistry"
    assert result.professional.registration_number == "DDS-48291"
    assert result.professional.registration_region == "CA"
    assert result.professional.email == "morgan@example.com"
    assert result.professional.phone == "+1-202-555-0130"
    assert result.professional.external_reference == "PROVIDER-100"
    assert result.professional.status is ProfessionalStatus.ACTIVE
    assert result.professional.version == 1
    assert result.professional.created_at == _PERSISTED_AT
    assert result.professional.updated_at == _PERSISTED_AT
    assert recording.events == ["add", "flush", "audit"]
    assert recording.flush_count == 1

    assert len(recorder.commands) == 1
    audit_command = recorder.commands[0]
    assert audit_command.tenant_id == tenant_id
    assert audit_command.action == AuditAction.PROFESSIONAL_CREATED.value
    assert audit_command.resource_type == AuditResourceType.PROFESSIONAL.value
    assert audit_command.resource_id == str(result.professional.id)
    assert audit_command.actor == audit_context.actor
    assert audit_command.source is AuditSource.HTTP
    assert audit_command.request_id == audit_context.request_id
    assert audit_command.correlation_id == audit_context.correlation_id
    assert audit_command.metadata == {"status": "active", "version": 1}
    assert audit_command.idempotency_key == (f"professional-created:{result.professional.id}")
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(audit_command.metadata)
    assert recorder.sessions == [session]


def test_create_professional_normalizes_profile_fields_before_persistence() -> None:
    recording = RecordingProfessionalRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=recording.events)
    service = _service(recording, session, recorder)

    service.execute(
        _command(
            full_name="  Morgan Reed  ",
            specialty="  Dentistry  ",
            registration_number="  DDS-48291  ",
            registration_region="  ca  ",
            email="  MORGAN@EXAMPLE.COM  ",
            phone="  +1-202-555-0130  ",
            external_reference="  PROVIDER-100  ",
        )
    )

    professional = recording.added_professional
    assert professional is not None
    assert professional.full_name == "Morgan Reed"
    assert professional.specialty == "Dentistry"
    assert professional.registration_number == "DDS-48291"
    assert professional.registration_region == "CA"
    assert professional.email == "morgan@example.com"
    assert professional.phone == "+1-202-555-0130"
    assert professional.external_reference == "PROVIDER-100"
    assert recording.events == ["add", "flush", "audit"]


def test_create_professional_preserves_optional_nulls() -> None:
    recording = RecordingProfessionalRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=recording.events)
    service = _service(recording, session, recorder)

    result = service.execute(
        _command(
            specialty=None,
            registration_number=None,
            registration_region=None,
            email=None,
            phone=None,
            external_reference=None,
        )
    )

    assert result.professional.membership_id is None
    assert result.professional.specialty is None
    assert result.professional.registration_number is None
    assert result.professional.registration_region is None
    assert result.professional.email is None
    assert result.professional.phone is None
    assert result.professional.external_reference is None


def test_create_professional_converts_blank_optional_fields_to_null() -> None:
    recording = RecordingProfessionalRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=recording.events)
    service = _service(recording, session, recorder)

    result = service.execute(
        _command(
            specialty="   ",
            registration_number="   ",
            registration_region="   ",
            email="   ",
            phone="   ",
            external_reference="   ",
        )
    )

    assert result.professional.specialty is None
    assert result.professional.registration_number is None
    assert result.professional.registration_region is None
    assert result.professional.email is None
    assert result.professional.phone is None
    assert result.professional.external_reference is None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("full_name", "   "),
        ("email", "not-an-email"),
    ],
)
def test_create_professional_rejects_invalid_input_before_persistence(
    field_name: str,
    value: str,
) -> None:
    recording = RecordingProfessionalRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ValueError):
        if field_name == "full_name":
            service.execute(_command(full_name=value))
        else:
            service.execute(_command(email=value))

    assert recording.added_professional is None
    assert recording.events == []
    assert recording.flush_count == 0
    assert recorder.commands == []


def test_create_professional_propagates_external_reference_conflict() -> None:
    error = ProfessionalExternalReferenceConflictError()
    recording = RecordingProfessionalRepository(flush_error=error)
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=recording.events)
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalExternalReferenceConflictError) as captured:
        service.execute(_command())

    assert captured.value is error
    assert recording.added_professional is not None
    assert recording.events == ["add", "flush"]
    assert recording.flush_count == 1
    assert recorder.commands == []


def test_create_professional_propagates_unexpected_flush_failure() -> None:
    error = RuntimeError("database unavailable")
    recording = RecordingProfessionalRepository(flush_error=error)
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=recording.events)
    service = _service(recording, session, recorder)

    with pytest.raises(RuntimeError) as captured:
        service.execute(_command())

    assert captured.value is error
    assert recording.added_professional is not None
    assert recording.events == ["add", "flush"]
    assert recording.flush_count == 1
    assert recorder.commands == []


def test_create_professional_propagates_audit_failure_after_flush() -> None:
    recording = RecordingProfessionalRepository()
    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(_command())

    assert recording.added_professional is not None
    assert recording.flush_count == 1
    assert recording.events == ["add", "flush"]
