from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.patients.contracts import (
    PatientRecord,
    RestorePatientCommand,
)
from clinicops.patients.enums import PatientStatus
from clinicops.patients.exceptions import (
    PatientNotArchivedError,
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.restore_patient import (
    RestorePatientService,
)


class RecordingPatientRepository:
    def __init__(
        self,
        *,
        reads: list[PatientRecord | None],
        restore_result: PatientRecord | None = None,
    ) -> None:
        self.reads = list(reads)
        self.restore_result = restore_result
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.restore_calls: list[tuple[UUID, UUID, int]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
    ) -> PatientRecord | None:
        self.get_calls.append((tenant_id, patient_id))
        if not self.reads:
            raise AssertionError("No configured patient read remains.")
        return self.reads.pop(0)

    def restore_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
        expected_version: int,
    ) -> PatientRecord | None:
        self.restore_calls.append((tenant_id, patient_id, expected_version))
        return self.restore_result


def _patient_record(
    *,
    tenant_id: UUID | None = None,
    patient_id: UUID | None = None,
    status: PatientStatus = PatientStatus.ARCHIVED,
    version: int = 3,
) -> PatientRecord:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    return PatientRecord(
        id=patient_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        full_name="Jordan Lee",
        date_of_birth=None,
        email=None,
        phone=None,
        external_reference=None,
        status=status,
        version=version,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _service(
    repository: RecordingPatientRepository,
) -> RestorePatientService:
    return RestorePatientService(cast(PatientRepository, repository))


def test_restore_patient_returns_restored_record() -> None:
    current = _patient_record()
    restored = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ACTIVE,
        version=4,
    )
    repository = RecordingPatientRepository(
        reads=[current],
        restore_result=restored,
    )
    service = _service(repository)

    result = service.execute(
        RestorePatientCommand(
            tenant_id=current.tenant_id,
            patient_id=current.id,
            expected_version=3,
        )
    )

    assert result.patient is restored
    assert repository.restore_calls == [(current.tenant_id, current.id, 3)]


def test_restore_patient_raises_not_found_for_invisible_patient() -> None:
    tenant_id = uuid4()
    patient_id = uuid4()
    repository = RecordingPatientRepository(reads=[None])
    service = _service(repository)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            RestorePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=1,
            )
        )

    assert repository.restore_calls == []


def test_restore_patient_rejects_stale_version_before_transition() -> None:
    current = _patient_record(version=4)
    repository = RecordingPatientRepository(reads=[current])
    service = _service(repository)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )

    assert repository.restore_calls == []


def test_restore_patient_rejects_active_patient() -> None:
    current = _patient_record(status=PatientStatus.ACTIVE)
    repository = RecordingPatientRepository(reads=[current])
    service = _service(repository)

    with pytest.raises(PatientNotArchivedError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )

    assert repository.restore_calls == []


def test_restore_patient_classifies_failed_transition_as_version_conflict() -> None:
    current = _patient_record(version=3)
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
        status=PatientStatus.ACTIVE,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        restore_result=None,
    )
    service = _service(repository)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )

    assert len(repository.get_calls) == 2
    assert len(repository.restore_calls) == 1


def test_restore_patient_classifies_failed_transition_as_not_found() -> None:
    current = _patient_record()
    repository = RecordingPatientRepository(
        reads=[current, None],
        restore_result=None,
    )
    service = _service(repository)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )


def test_restore_patient_classifies_same_version_active_state() -> None:
    current = _patient_record()
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ACTIVE,
        version=3,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        restore_result=None,
    )
    service = _service(repository)

    with pytest.raises(PatientNotArchivedError):
        service.execute(
            RestorePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )
