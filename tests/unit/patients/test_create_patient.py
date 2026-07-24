from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand, RecordedAuditLog
from clinicops.audit.enums import AuditSource
from clinicops.audit.recording import AuditRecorder
from clinicops.patients.contracts import (
    CreatedPatient,
    CreatePatientCommand,
)
from clinicops.patients.enums import PatientStatus
from clinicops.patients.exceptions import (
    PatientExternalReferenceConflictError,
    PatientInvalidDateOfBirthError,
)
from clinicops.patients.models import Patient
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.create_patient import (
    CreatePatientService,
)

_PERSISTED_AT = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
_RECORDED_AT = datetime(2026, 7, 23, 21, 31, tzinfo=UTC)


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class RecordingPatientRepository:
    def __init__(
        self,
        *,
        flush_error: Exception | None = None,
    ) -> None:
        self.added_patient: Patient | None = None
        self.flush_error = flush_error
        self.flush_count = 0
        self.events: list[str] = []

    def add(self, patient: Patient) -> None:
        self.events.append("add")
        self.added_patient = patient

    def flush(self) -> None:
        self.events.append("flush")
        self.flush_count += 1

        if self.flush_error is not None:
            raise self.flush_error

        patient = self.added_patient
        if patient is None:
            raise AssertionError("A patient must be added before flush.")

        patient.id = uuid4()
        patient.status = PatientStatus.ACTIVE
        patient.version = 1
        patient.created_at = _PERSISTED_AT
        patient.updated_at = _PERSISTED_AT


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
        role="owner",
        request_id="request-patient-create",
        correlation_id="correlation-patient-create",
    )


def _service(
    repository: RecordingPatientRepository,
    session: Session,
    recorder: RecordingAuditRecorder | FailingAuditRecorder,
) -> CreatePatientService:
    return CreatePatientService(
        cast(PatientRepository, repository),
        session,
        cast(AuditRecorder, recorder),
    )


def test_create_patient_returns_persisted_patient_record() -> None:
    tenant_id = uuid4()
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=repository.events)
    service = _service(repository, session, recorder)

    result = service.execute(
        CreatePatientCommand(
            tenant_id=tenant_id,
            full_name="Jordan Lee",
            audit_context=_audit_context(),
            date_of_birth=date(1992, 8, 14),
            email="jordan.lee@example.com",
            phone="+1-202-555-0184",
            external_reference="LEGACY-10482",
        )
    )

    assert isinstance(result, CreatedPatient)
    assert result.patient.tenant_id == tenant_id
    assert result.patient.full_name == "Jordan Lee"
    assert result.patient.date_of_birth == date(1992, 8, 14)
    assert result.patient.email == "jordan.lee@example.com"
    assert result.patient.phone == "+1-202-555-0184"
    assert result.patient.external_reference == "LEGACY-10482"
    assert result.patient.status is PatientStatus.ACTIVE
    assert result.patient.version == 1
    assert result.patient.created_at == _PERSISTED_AT
    assert result.patient.updated_at == _PERSISTED_AT

    assert len(recorder.commands) == 1
    command = recorder.commands[0]
    assert command.tenant_id == tenant_id
    assert command.action == AuditAction.PATIENT_CREATED.value
    assert command.resource_type == AuditResourceType.PATIENT.value
    assert command.resource_id == str(result.patient.id)
    assert command.source is AuditSource.HTTP
    assert command.request_id == "request-patient-create"
    assert command.correlation_id == "correlation-patient-create"
    assert command.metadata == {"status": "active", "version": 1}
    assert command.idempotency_key == f"patient-created:{result.patient.id}"
    assert recorder.sessions == [session]
    assert "full_name" not in command.metadata
    assert "email" not in command.metadata
    assert "phone" not in command.metadata
    assert "date_of_birth" not in command.metadata
    assert "external_reference" not in command.metadata
    assert repository.events == ["add", "flush", "audit"]


def test_create_patient_normalizes_values_before_persistence() -> None:
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=repository.events)
    service = _service(repository, session, recorder)

    service.execute(
        CreatePatientCommand(
            tenant_id=uuid4(),
            full_name="  Jordan  Lee  ",
            audit_context=_audit_context(),
            email="  Jordan.Lee@Example.COM  ",
            phone="  +55 (51) 99999-0000  ",
            external_reference="  Legacy-AbC-10  ",
        )
    )

    patient = repository.added_patient
    assert patient is not None
    assert patient.full_name == "Jordan  Lee"
    assert patient.email == "jordan.lee@example.com"
    assert patient.phone == "+55 (51) 99999-0000"
    assert patient.external_reference == "Legacy-AbC-10"


def test_create_patient_preserves_optional_nulls() -> None:
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=repository.events)
    service = _service(repository, session, recorder)

    result = service.execute(
        CreatePatientCommand(
            tenant_id=uuid4(),
            full_name="Morgan Ellis",
            audit_context=_audit_context(),
        )
    )

    assert result.patient.date_of_birth is None
    assert result.patient.email is None
    assert result.patient.phone is None
    assert result.patient.external_reference is None


@pytest.mark.parametrize(
    "full_name",
    [
        "",
        "   ",
        "x" * 201,
    ],
)
def test_create_patient_rejects_invalid_full_name_before_persistence(
    full_name: str,
) -> None:
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(ValueError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name=full_name,
                audit_context=_audit_context(),
            )
        )

    assert repository.added_patient is None
    assert repository.flush_count == 0
    assert repository.events == []
    assert recorder.commands == []


@pytest.mark.parametrize(
    "email",
    [
        "",
        "invalid-email",
        f"{'a' * 320}@example.com",
    ],
)
def test_create_patient_rejects_invalid_email_before_persistence(
    email: str,
) -> None:
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(ValueError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                audit_context=_audit_context(),
                email=email,
            )
        )

    assert repository.added_patient is None
    assert repository.flush_count == 0
    assert recorder.commands == []


@pytest.mark.parametrize(
    ("phone", "external_reference"),
    [
        ("", None),
        ("1" * 51, None),
        (None, ""),
        (None, "x" * 101),
    ],
)
def test_create_patient_rejects_invalid_optional_text_before_persistence(
    phone: str | None,
    external_reference: str | None,
) -> None:
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(ValueError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                audit_context=_audit_context(),
                phone=phone,
                external_reference=external_reference,
            )
        )

    assert repository.added_patient is None
    assert repository.flush_count == 0
    assert recorder.commands == []


def test_create_patient_rejects_future_date_before_persistence() -> None:
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder()
    service = _service(repository, session, recorder)
    future_date = date.today() + timedelta(days=1)

    with pytest.raises(PatientInvalidDateOfBirthError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                audit_context=_audit_context(),
                date_of_birth=future_date,
            )
        )

    assert repository.added_patient is None
    assert repository.flush_count == 0
    assert recorder.commands == []


def test_create_patient_propagates_external_reference_conflict() -> None:
    conflict = PatientExternalReferenceConflictError()
    repository = RecordingPatientRepository(
        flush_error=conflict,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=repository.events)
    service = _service(repository, session, recorder)

    with pytest.raises(PatientExternalReferenceConflictError) as error:
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                audit_context=_audit_context(),
                external_reference="LEGACY-CONFLICT",
            )
        )

    assert error.value is conflict
    assert repository.added_patient is not None
    assert repository.flush_count == 1
    assert repository.events == ["add", "flush"]
    assert recorder.commands == []


def test_create_patient_propagates_unrelated_flush_error() -> None:
    failure = RuntimeError("database failure")
    repository = RecordingPatientRepository(
        flush_error=failure,
    )
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=repository.events)
    service = _service(repository, session, recorder)

    with pytest.raises(RuntimeError) as error:
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                audit_context=_audit_context(),
            )
        )

    assert error.value is failure
    assert repository.flush_count == 1
    assert recorder.commands == []


def test_create_patient_calls_add_then_flush_once() -> None:
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = RecordingAuditRecorder(events=repository.events)
    service = _service(repository, session, recorder)

    service.execute(
        CreatePatientCommand(
            tenant_id=uuid4(),
            full_name="Jordan Lee",
            audit_context=_audit_context(),
        )
    )

    assert repository.events == ["add", "flush", "audit"]
    assert repository.flush_count == 1


def test_create_patient_propagates_audit_failure_after_flush() -> None:
    repository = RecordingPatientRepository()
    session = cast(Session, object())
    recorder = FailingAuditRecorder()
    service = _service(repository, session, recorder)

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                audit_context=_audit_context(),
            )
        )

    assert repository.added_patient is not None
    assert repository.flush_count == 1
    assert repository.events == ["add", "flush"]
