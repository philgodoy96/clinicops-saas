from clinicops.patients.contracts import (
    GetPatientCommand,
    PatientRecord,
)
from clinicops.patients.exceptions import PatientNotFoundError
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)


class GetPatientService:
    """Retrieve one patient through an explicit tenant boundary."""

    def __init__(
        self,
        repository: PatientRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: GetPatientCommand,
    ) -> PatientRecord:
        """Return a tenant-owned patient or raise not found."""

        patient = self._repository.get_by_id_for_tenant(
            tenant_id=command.tenant_id,
            patient_id=command.patient_id,
        )
        if patient is None:
            raise PatientNotFoundError

        return patient


__all__ = ["GetPatientService"]
