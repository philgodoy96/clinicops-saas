from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

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


class RecordingProfessionalRepository:
    """Record creation persistence interactions without a database."""

    def __init__(
        self,
        *,
        flush_error: Exception | None = None,
    ) -> None:
        self.added_professional: Professional | None = None
        self.call_order: list[str] = []
        self.flush_count = 0
        self._flush_error = flush_error

    def add(self, professional: Professional) -> None:
        self.call_order.append("add")
        self.added_professional = professional

    def flush(self) -> None:
        self.call_order.append("flush")
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


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _command(
    *,
    tenant_id: UUID | None = None,
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
        specialty=specialty,
        registration_number=registration_number,
        registration_region=registration_region,
        email=email,
        phone=phone,
        external_reference=external_reference,
    )


def test_create_professional_persists_and_returns_domain_result() -> None:
    recording = RecordingProfessionalRepository()
    service = CreateProfessionalService(_repository(recording))
    command = _command()

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
    assert recording.call_order == ["add", "flush"]
    assert recording.flush_count == 1


def test_create_professional_normalizes_profile_fields_before_persistence() -> None:
    recording = RecordingProfessionalRepository()
    service = CreateProfessionalService(_repository(recording))

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


def test_create_professional_preserves_optional_nulls() -> None:
    recording = RecordingProfessionalRepository()
    service = CreateProfessionalService(_repository(recording))

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
    service = CreateProfessionalService(_repository(recording))

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
    service = CreateProfessionalService(_repository(recording))

    with pytest.raises(ValueError):
        if field_name == "full_name":
            service.execute(_command(full_name=value))
        else:
            service.execute(_command(email=value))

    assert recording.added_professional is None
    assert recording.call_order == []
    assert recording.flush_count == 0


def test_create_professional_propagates_external_reference_conflict() -> None:
    error = ProfessionalExternalReferenceConflictError()
    recording = RecordingProfessionalRepository(flush_error=error)
    service = CreateProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalExternalReferenceConflictError) as captured:
        service.execute(_command())

    assert captured.value is error
    assert recording.added_professional is not None
    assert recording.call_order == ["add", "flush"]
    assert recording.flush_count == 1


def test_create_professional_propagates_unexpected_flush_failure() -> None:
    error = RuntimeError("database unavailable")
    recording = RecordingProfessionalRepository(flush_error=error)
    service = CreateProfessionalService(_repository(recording))

    with pytest.raises(RuntimeError) as captured:
        service.execute(_command())

    assert captured.value is error
    assert recording.added_professional is not None
    assert recording.call_order == ["add", "flush"]
    assert recording.flush_count == 1
