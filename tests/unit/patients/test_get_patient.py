from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.patients.contracts import (
    GetPatientCommand,
    PatientRecord,
)
from clinicops.patients.enums import PatientStatus
from clinicops.patients.exceptions import PatientNotFoundError
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.get_patient import (
    GetPatientService,
)


class RecordingPatientRepository:
    def __init__(
        self,
        *,
        result: PatientRecord | None,
    ) -> None:
        self.result = result
        self.calls: list[tuple[UUID, UUID]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
    ) -> PatientRecord | None:
        self.calls.append((tenant_id, patient_id))
        return self.result


def _patient_record(
    *,
    tenant_id: UUID,
    status: PatientStatus = PatientStatus.ACTIVE,
) -> PatientRecord:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    return PatientRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        full_name="Jordan Lee",
        date_of_birth=None,
        email=None,
        phone=None,
        external_reference=None,
        status=status,
        version=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _service(
    repository: RecordingPatientRepository,
) -> GetPatientService:
    return GetPatientService(cast(PatientRepository, repository))


def test_get_patient_returns_tenant_scoped_record() -> None:
    tenant_id = uuid4()
    patient = _patient_record(tenant_id=tenant_id)
    repository = RecordingPatientRepository(result=patient)
    service = _service(repository)

    result = service.execute(
        GetPatientCommand(
            tenant_id=tenant_id,
            patient_id=patient.id,
        )
    )

    assert result is patient
    assert repository.calls == [(tenant_id, patient.id)]


def test_get_patient_returns_archived_record() -> None:
    tenant_id = uuid4()
    patient = _patient_record(
        tenant_id=tenant_id,
        status=PatientStatus.ARCHIVED,
    )
    repository = RecordingPatientRepository(result=patient)
    service = _service(repository)

    result = service.execute(
        GetPatientCommand(
            tenant_id=tenant_id,
            patient_id=patient.id,
        )
    )

    assert result.status is PatientStatus.ARCHIVED


def test_get_patient_raises_not_found_for_invisible_record() -> None:
    tenant_id = uuid4()
    patient_id = uuid4()
    repository = RecordingPatientRepository(result=None)
    service = _service(repository)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            GetPatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
            )
        )

    assert repository.calls == [(tenant_id, patient_id)]
