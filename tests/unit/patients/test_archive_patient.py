from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.patients.contracts import (
    ArchivePatientCommand,
    PatientRecord,
)
from clinicops.patients.enums import PatientStatus
from clinicops.patients.exceptions import (
    PatientAlreadyArchivedError,
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.archive_patient import (
    ArchivePatientService,
)


class RecordingPatientRepository:
    def __init__(
        self,
        *,
        reads: list[PatientRecord | None],
        archive_result: PatientRecord | None = None,
    ) -> None:
        self.reads = list(reads)
        self.archive_result = archive_result
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.archive_calls: list[tuple[UUID, UUID, int]] = []

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

    def archive_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
        expected_version: int,
    ) -> PatientRecord | None:
        self.archive_calls.append((tenant_id, patient_id, expected_version))
        return self.archive_result


def _patient_record(
    *,
    tenant_id: UUID | None = None,
    patient_id: UUID | None = None,
    status: PatientStatus = PatientStatus.ACTIVE,
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
) -> ArchivePatientService:
    return ArchivePatientService(cast(PatientRepository, repository))


def test_archive_patient_returns_archived_record() -> None:
    current = _patient_record()
    archived = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ARCHIVED,
        version=4,
    )
    repository = RecordingPatientRepository(
        reads=[current],
        archive_result=archived,
    )
    service = _service(repository)

    result = service.execute(
        ArchivePatientCommand(
            tenant_id=current.tenant_id,
            patient_id=current.id,
            expected_version=3,
        )
    )

    assert result.patient is archived
    assert repository.archive_calls == [(current.tenant_id, current.id, 3)]


def test_archive_patient_raises_not_found_for_invisible_patient() -> None:
    tenant_id = uuid4()
    patient_id = uuid4()
    repository = RecordingPatientRepository(reads=[None])
    service = _service(repository)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=1,
            )
        )

    assert repository.archive_calls == []


def test_archive_patient_rejects_stale_version_before_transition() -> None:
    current = _patient_record(version=4)
    repository = RecordingPatientRepository(reads=[current])
    service = _service(repository)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )

    assert repository.archive_calls == []


def test_archive_patient_rejects_already_archived_patient() -> None:
    current = _patient_record(
        status=PatientStatus.ARCHIVED,
    )
    repository = RecordingPatientRepository(reads=[current])
    service = _service(repository)

    with pytest.raises(PatientAlreadyArchivedError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )

    assert repository.archive_calls == []


def test_archive_patient_classifies_failed_transition_as_version_conflict() -> None:
    current = _patient_record(version=3)
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        version=4,
        status=PatientStatus.ARCHIVED,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        archive_result=None,
    )
    service = _service(repository)

    with pytest.raises(PatientVersionConflictError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )

    assert len(repository.get_calls) == 2
    assert len(repository.archive_calls) == 1


def test_archive_patient_classifies_failed_transition_as_not_found() -> None:
    current = _patient_record()
    repository = RecordingPatientRepository(
        reads=[current, None],
        archive_result=None,
    )
    service = _service(repository)

    with pytest.raises(PatientNotFoundError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )


def test_archive_patient_classifies_same_version_archived_state() -> None:
    current = _patient_record()
    latest = _patient_record(
        tenant_id=current.tenant_id,
        patient_id=current.id,
        status=PatientStatus.ARCHIVED,
        version=3,
    )
    repository = RecordingPatientRepository(
        reads=[current, latest],
        archive_result=None,
    )
    service = _service(repository)

    with pytest.raises(PatientAlreadyArchivedError):
        service.execute(
            ArchivePatientCommand(
                tenant_id=current.tenant_id,
                patient_id=current.id,
                expected_version=3,
            )
        )
