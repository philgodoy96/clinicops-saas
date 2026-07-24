from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest

from clinicops.patients.contracts import (
    CreatePatientCommand,
    CreatedPatient,
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


def _service(
    repository: RecordingPatientRepository,
) -> CreatePatientService:
    return CreatePatientService(cast(PatientRepository, repository))


def test_create_patient_returns_persisted_patient_record() -> None:
    tenant_id = uuid4()
    repository = RecordingPatientRepository()
    service = _service(repository)

    result = service.execute(
        CreatePatientCommand(
            tenant_id=tenant_id,
            full_name="Jordan Lee",
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


def test_create_patient_normalizes_values_before_persistence() -> None:
    repository = RecordingPatientRepository()
    service = _service(repository)

    service.execute(
        CreatePatientCommand(
            tenant_id=uuid4(),
            full_name="  Jordan  Lee  ",
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
    service = _service(repository)

    result = service.execute(
        CreatePatientCommand(
            tenant_id=uuid4(),
            full_name="Morgan Ellis",
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
    service = _service(repository)

    with pytest.raises(ValueError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name=full_name,
            )
        )

    assert repository.added_patient is None
    assert repository.flush_count == 0
    assert repository.events == []


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
    service = _service(repository)

    with pytest.raises(ValueError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                email=email,
            )
        )

    assert repository.added_patient is None
    assert repository.flush_count == 0


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
    service = _service(repository)

    with pytest.raises(ValueError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                phone=phone,
                external_reference=external_reference,
            )
        )

    assert repository.added_patient is None
    assert repository.flush_count == 0


def test_create_patient_rejects_future_date_before_persistence() -> None:
    repository = RecordingPatientRepository()
    service = _service(repository)
    future_date = date.today() + timedelta(days=1)

    with pytest.raises(PatientInvalidDateOfBirthError):
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                date_of_birth=future_date,
            )
        )

    assert repository.added_patient is None
    assert repository.flush_count == 0


def test_create_patient_propagates_external_reference_conflict() -> None:
    conflict = PatientExternalReferenceConflictError()
    repository = RecordingPatientRepository(
        flush_error=conflict,
    )
    service = _service(repository)

    with pytest.raises(PatientExternalReferenceConflictError) as error:
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
                external_reference="LEGACY-CONFLICT",
            )
        )

    assert error.value is conflict
    assert repository.added_patient is not None
    assert repository.flush_count == 1
    assert repository.events == ["add", "flush"]


def test_create_patient_propagates_unrelated_flush_error() -> None:
    failure = RuntimeError("database failure")
    repository = RecordingPatientRepository(
        flush_error=failure,
    )
    service = _service(repository)

    with pytest.raises(RuntimeError) as error:
        service.execute(
            CreatePatientCommand(
                tenant_id=uuid4(),
                full_name="Jordan Lee",
            )
        )

    assert error.value is failure
    assert repository.flush_count == 1


def test_create_patient_calls_add_then_flush_once() -> None:
    repository = RecordingPatientRepository()
    service = _service(repository)

    service.execute(
        CreatePatientCommand(
            tenant_id=uuid4(),
            full_name="Jordan Lee",
        )
    )

    assert repository.events == ["add", "flush"]
    assert repository.flush_count == 1
