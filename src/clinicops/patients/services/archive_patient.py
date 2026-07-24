from typing import NoReturn

from clinicops.patients.contracts import (
    ArchivePatientCommand,
    ArchivedPatient,
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


class ArchivePatientService:
    """Archive an active patient using optimistic concurrency."""

    def __init__(
        self,
        repository: PatientRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: ArchivePatientCommand,
    ) -> ArchivedPatient:
        """Archive a tenant-owned patient at the observed version."""

        current = self._repository.get_by_id_for_tenant(
            tenant_id=command.tenant_id,
            patient_id=command.patient_id,
        )
        if current is None:
            raise PatientNotFoundError

        _require_current_version(
            current,
            expected_version=command.expected_version,
        )
        _require_active_patient(current)

        archived = self._repository.archive_for_tenant(
            tenant_id=command.tenant_id,
            patient_id=command.patient_id,
            expected_version=command.expected_version,
        )
        if archived is None:
            _raise_failed_archive(
                repository=self._repository,
                command=command,
            )

        return ArchivedPatient(patient=archived)


def _require_current_version(
    patient: PatientRecord,
    *,
    expected_version: int,
) -> None:
    if patient.version != expected_version:
        raise PatientVersionConflictError


def _require_active_patient(patient: PatientRecord) -> None:
    if patient.status is PatientStatus.ARCHIVED:
        raise PatientAlreadyArchivedError


def _raise_failed_archive(
    *,
    repository: PatientRepository,
    command: ArchivePatientCommand,
) -> NoReturn:
    latest = repository.get_by_id_for_tenant(
        tenant_id=command.tenant_id,
        patient_id=command.patient_id,
    )
    if latest is None:
        raise PatientNotFoundError

    _require_current_version(
        latest,
        expected_version=command.expected_version,
    )
    _require_active_patient(latest)

    raise PatientVersionConflictError


__all__ = ["ArchivePatientService"]
