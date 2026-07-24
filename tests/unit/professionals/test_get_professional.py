from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.professionals.contracts import (
    GetProfessionalCommand,
    ProfessionalRecord,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import ProfessionalNotFoundError
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.get_professional import (
    GetProfessionalService,
)

_RECORDED_AT = datetime(2026, 7, 24, 19, 0, tzinfo=UTC)


class RecordingProfessionalRepository:
    """Record tenant-scoped professional retrieval interactions."""

    def __init__(
        self,
        *,
        professional: ProfessionalRecord | None,
    ) -> None:
        self._professional = professional
        self.calls: list[tuple[UUID, UUID]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.calls.append((tenant_id, professional_id))
        return self._professional


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _professional_record(
    *,
    tenant_id: UUID,
    professional_id: UUID,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=professional_id,
        tenant_id=tenant_id,
        membership_id=None,
        full_name="Morgan Reed",
        specialty="Dentistry",
        registration_number="DDS-48291",
        registration_region="CA",
        email="morgan@example.com",
        phone="+1-202-555-0130",
        external_reference="PROVIDER-100",
        status=status,
        version=1,
        created_at=_RECORDED_AT,
        updated_at=_RECORDED_AT,
    )


def test_get_professional_returns_tenant_scoped_record() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    professional = _professional_record(
        tenant_id=tenant_id,
        professional_id=professional_id,
    )
    recording = RecordingProfessionalRepository(
        professional=professional,
    )
    service = GetProfessionalService(_repository(recording))

    result = service.execute(
        GetProfessionalCommand(
            tenant_id=tenant_id,
            professional_id=professional_id,
        )
    )

    assert result is professional
    assert recording.calls == [(tenant_id, professional_id)]


def test_get_professional_allows_archived_record() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    professional = _professional_record(
        tenant_id=tenant_id,
        professional_id=professional_id,
        status=ProfessionalStatus.ARCHIVED,
    )
    recording = RecordingProfessionalRepository(
        professional=professional,
    )
    service = GetProfessionalService(_repository(recording))

    result = service.execute(
        GetProfessionalCommand(
            tenant_id=tenant_id,
            professional_id=professional_id,
        )
    )

    assert result.status is ProfessionalStatus.ARCHIVED
    assert recording.calls == [(tenant_id, professional_id)]


def test_get_professional_raises_not_found_for_invisible_record() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(professional=None)
    service = GetProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            GetProfessionalCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
            )
        )

    assert recording.calls == [(tenant_id, professional_id)]


def test_get_professional_propagates_repository_failure() -> None:
    class FailingProfessionalRepository:
        def get_by_id_for_tenant(
            self,
            *,
            tenant_id: UUID,
            professional_id: UUID,
        ) -> ProfessionalRecord | None:
            del tenant_id, professional_id
            raise RuntimeError("database unavailable")

    service = GetProfessionalService(cast(ProfessionalRepository, FailingProfessionalRepository()))

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.execute(
            GetProfessionalCommand(
                tenant_id=uuid4(),
                professional_id=uuid4(),
            )
        )
