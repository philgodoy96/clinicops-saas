from collections.abc import Callable, Mapping
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
    UpdateProfessionalCommand,
)
from clinicops.professionals.enums import (
    ProfessionalMutableField,
    ProfessionalStatus,
)
from clinicops.professionals.exceptions import (
    ProfessionalExternalReferenceConflictError,
    ProfessionalInvalidUpdateError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.update_professional import (
    UpdateProfessionalService,
)

_RECORDED_AT = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
_AUDIT_RECORDED_AT = datetime(2026, 7, 24, 20, 1, tzinfo=UTC)
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
    """Record optimistic professional update interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        updated: ProfessionalRecord | None = None,
        update_error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._updated = updated
        self._update_error = update_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.update_calls: list[
            tuple[
                UUID,
                UUID,
                int,
                dict[ProfessionalMutableField, object],
            ]
        ] = []
        self._events = events

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def update_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
        values: Mapping[ProfessionalMutableField, object],
    ) -> ProfessionalRecord | None:
        self.update_calls.append(
            (
                tenant_id,
                professional_id,
                expected_version,
                dict(values),
            )
        )
        if self._events is not None:
            self._events.append("update")
        if self._update_error is not None:
            raise self._update_error
        return self._updated


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
        request_id="request-professional-update",
        correlation_id="correlation-professional-update",
    )


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _service(
    repository: RecordingProfessionalRepository,
    session: Session,
    recorder: RecordingAuditRecorder | FailingAuditRecorder,
) -> UpdateProfessionalService:
    return UpdateProfessionalService(
        _repository(repository),
        session,
        cast(AuditRecorder, recorder),
    )


def _professional_record(
    *,
    tenant_id: UUID | None = None,
    professional_id: UUID | None = None,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    version: int = 1,
    full_name: str = "Morgan Reed",
    specialty: str | None = "Dentistry",
    registration_number: str | None = "DDS-48291",
    registration_region: str | None = "CA",
    email: str | None = "morgan@example.com",
    phone: str | None = "+1-202-555-0130",
    external_reference: str | None = "PROVIDER-100",
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=professional_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        membership_id=None,
        full_name=full_name,
        specialty=specialty,
        registration_number=registration_number,
        registration_region=registration_region,
        email=email,
        phone=phone,
        external_reference=external_reference,
        status=status,
        version=version,
        created_at=_RECORDED_AT,
        updated_at=_RECORDED_AT,
    )


def _command(
    current: ProfessionalRecord,
    *,
    expected_version: int | None = None,
    fields_to_update: frozenset[ProfessionalMutableField],
    audit_context: AuditRecordingContext | None = None,
    full_name: str | None = None,
    specialty: str | None = None,
    registration_number: str | None = None,
    registration_region: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    external_reference: str | None = None,
) -> UpdateProfessionalCommand:
    return UpdateProfessionalCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        expected_version=(current.version if expected_version is None else expected_version),
        fields_to_update=fields_to_update,
        audit_context=audit_context or _audit_context(),
        full_name=full_name,
        specialty=specialty,
        registration_number=registration_number,
        registration_region=registration_region,
        email=email,
        phone=phone,
        external_reference=external_reference,
    )


def test_update_professional_normalizes_and_updates_changed_fields() -> None:
    current = _professional_record()
    updated = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
        full_name="Morgan A. Reed",
        specialty="Orthodontics",
        registration_region="NY",
        email="new@example.com",
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        updated=updated,
        events=events,
    )
    session = cast(Session, object())
    audit_context = _audit_context()
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)
    command = _command(
        current,
        audit_context=audit_context,
        fields_to_update=frozenset(
            {
                ProfessionalMutableField.EMAIL,
                ProfessionalMutableField.REGISTRATION_REGION,
                ProfessionalMutableField.SPECIALTY,
                ProfessionalMutableField.FULL_NAME,
            }
        ),
        full_name="  Morgan A. Reed  ",
        specialty="  Orthodontics  ",
        registration_region="  ny  ",
        email="  NEW@EXAMPLE.COM  ",
    )

    result = service.execute(command)

    assert result.professional is updated
    assert result.changed_fields == (
        ProfessionalMutableField.FULL_NAME,
        ProfessionalMutableField.SPECIALTY,
        ProfessionalMutableField.REGISTRATION_REGION,
        ProfessionalMutableField.EMAIL,
    )
    assert recording.get_calls == [(current.tenant_id, current.id)]
    assert recording.update_calls == [
        (
            current.tenant_id,
            current.id,
            1,
            {
                ProfessionalMutableField.FULL_NAME: "Morgan A. Reed",
                ProfessionalMutableField.SPECIALTY: "Orthodontics",
                ProfessionalMutableField.REGISTRATION_REGION: "NY",
                ProfessionalMutableField.EMAIL: "new@example.com",
            },
        )
    ]
    assert events == ["update", "audit"]

    assert len(recorder.commands) == 1
    audit_command = recorder.commands[0]
    assert audit_command.tenant_id == current.tenant_id
    assert audit_command.action == AuditAction.PROFESSIONAL_UPDATED.value
    assert audit_command.action == "professional.updated"
    assert audit_command.resource_type == AuditResourceType.PROFESSIONAL.value
    assert audit_command.resource_id == str(updated.id)
    assert audit_command.actor == audit_context.actor
    assert audit_command.source is AuditSource.HTTP
    assert audit_command.request_id == audit_context.request_id
    assert audit_command.correlation_id == audit_context.correlation_id
    assert audit_command.metadata == {
        "version": 2,
        "changed_fields": [
            "full_name",
            "specialty",
            "registration_region",
            "email",
        ],
    }
    assert audit_command.idempotency_key == (f"professional-updated:{updated.id}:2")
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(audit_command.metadata)
    assert recorder.sessions == [session]


@pytest.mark.parametrize(
    "field",
    [
        ProfessionalMutableField.SPECIALTY,
        ProfessionalMutableField.REGISTRATION_NUMBER,
        ProfessionalMutableField.REGISTRATION_REGION,
        ProfessionalMutableField.EMAIL,
        ProfessionalMutableField.PHONE,
        ProfessionalMutableField.EXTERNAL_REFERENCE,
    ],
)
def test_update_professional_allows_explicit_null_for_nullable_fields(
    field: ProfessionalMutableField,
) -> None:
    current = _professional_record()
    updated = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        updated=updated,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    result = service.execute(
        _command(
            current,
            fields_to_update=frozenset({field}),
        )
    )

    assert result.changed_fields == (field,)
    assert recording.update_calls[0][3] == {field: None}
    assert len(recorder.commands) == 1
    assert recorder.commands[0].metadata == {
        "version": 2,
        "changed_fields": [field.value],
    }


def test_update_professional_sends_only_effectively_changed_fields() -> None:
    current = _professional_record()
    updated = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
        email="changed@example.com",
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        updated=updated,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    result = service.execute(
        _command(
            current,
            fields_to_update=frozenset(
                {
                    ProfessionalMutableField.FULL_NAME,
                    ProfessionalMutableField.EMAIL,
                }
            ),
            full_name="  Morgan Reed  ",
            email="changed@example.com",
        )
    )

    assert result.changed_fields == (ProfessionalMutableField.EMAIL,)
    assert recording.update_calls[0][3] == {ProfessionalMutableField.EMAIL: "changed@example.com"}
    assert recorder.commands[0].metadata["changed_fields"] == ["email"]


@pytest.mark.parametrize(
    "command_factory",
    [
        lambda current: _command(
            current,
            fields_to_update=frozenset(),
        ),
        lambda current: _command(
            current,
            fields_to_update=frozenset({ProfessionalMutableField.FULL_NAME}),
            full_name=None,
        ),
        lambda current: _command(
            current,
            fields_to_update=frozenset({ProfessionalMutableField.FULL_NAME}),
            full_name="   ",
        ),
        lambda current: _command(
            current,
            fields_to_update=frozenset({ProfessionalMutableField.EMAIL}),
            email="not-an-email",
        ),
        lambda current: _command(
            current,
            fields_to_update=frozenset({ProfessionalMutableField.FULL_NAME}),
            full_name="  Morgan Reed  ",
        ),
    ],
)
def test_update_professional_rejects_invalid_or_no_op_patch(
    command_factory: Callable[[ProfessionalRecord], UpdateProfessionalCommand],
) -> None:
    current = _professional_record()
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalInvalidUpdateError):
        service.execute(command_factory(current))

    assert recording.update_calls == []
    assert recorder.commands == []


def test_update_professional_rejects_archived_record() -> None:
    current = _professional_record(status=ProfessionalStatus.ARCHIVED)
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalInvalidUpdateError):
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert recording.update_calls == []
    assert recorder.commands == []


def test_update_professional_rejects_stale_version_before_mutation() -> None:
    current = _professional_record(version=4)
    recording = RecordingProfessionalRepository(reads=[current])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(
            _command(
                current,
                expected_version=3,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert recording.update_calls == []
    assert recorder.commands == []


def test_update_professional_raises_not_found_for_invisible_record() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            UpdateProfessionalCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                expected_version=1,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                audit_context=_audit_context(),
                specialty="Orthodontics",
            )
        )

    assert recording.get_calls == [(tenant_id, professional_id)]
    assert recording.update_calls == []
    assert recorder.commands == []


def test_update_professional_classifies_concurrent_winner_as_version_conflict() -> None:
    current = _professional_record(version=1)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        updated=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert recording.get_calls == [
        (current.tenant_id, current.id),
        (current.tenant_id, current.id),
    ]
    assert len(recording.update_calls) == 1
    assert recorder.commands == []


def test_update_professional_classifies_concurrent_archive_as_version_conflict() -> None:
    current = _professional_record(version=1)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        updated=None,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert recorder.commands == []


def test_update_professional_propagates_external_reference_conflict() -> None:
    current = _professional_record()
    error = ProfessionalExternalReferenceConflictError()
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        update_error=error,
        events=events,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=events)
    service = _service(recording, session, recorder)

    with pytest.raises(ProfessionalExternalReferenceConflictError) as captured:
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.EXTERNAL_REFERENCE}),
                external_reference="PROVIDER-200",
            )
        )

    assert captured.value is error
    assert len(recording.update_calls) == 1
    assert events == ["update"]
    assert recorder.commands == []


def test_update_professional_propagates_audit_failure_after_update() -> None:
    current = _professional_record()
    updated = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
        specialty="Orthodontics",
    )
    events: list[str] = []
    recording = RecordingProfessionalRepository(
        reads=[current],
        updated=updated,
        events=events,
    )
    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(recording, session, recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert len(recording.update_calls) == 1
    assert events == ["update"]
