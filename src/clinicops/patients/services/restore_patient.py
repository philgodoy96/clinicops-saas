from typing import NoReturn

from clinicops.patients.contracts import (
    PatientRecord,
    RestorePatientCommand,
    RestoredPatient,
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


class RestorePatientService:
    """Restore an archived patient using optimistic concurrency."""

    def __init__(
        self,
        repository: PatientRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: RestorePatientCommand,
    ) -> RestoredPatient:
        """Restore a tenant-owned patient at the observed version."""

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
        _require_archived_patient(current)

        restored = self._repository.restore_for_tenant(
            tenant_id=command.tenant_id,
            patient_id=command.patient_id,
            expected_version=command.expected_version,
        )
        if restored is None:
            _raise_failed_restore(
                repository=self._repository,
                command=command,
            )

        return RestoredPatient(patient=restored)


def _require_current_version(
    patient: PatientRecord,
    *,
    expected_version: int,
) -> None:
    if patient.version != expected_version:
        raise PatientVersionConflictError


def _require_archived_patient(patient: PatientRecord) -> None:
    if patient.status is PatientStatus.ACTIVE:
        raise PatientNotArchivedError


def _raise_failed_restore(
    *,
    repository: PatientRepository,
    command: RestorePatientCommand,
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
    _require_archived_patient(latest)

    raise PatientVersionConflictError


__all__ = ["RestorePatientService"]
