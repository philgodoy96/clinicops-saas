from clinicops.patients.contracts import (
    CreatePatientCommand,
    CreatedPatient,
    PatientRecord,
)
from clinicops.patients.models import Patient
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.validation import (
    normalize_email,
    normalize_external_reference,
    normalize_full_name,
    normalize_phone,
    validate_date_of_birth,
)


class CreatePatientService:
    """Create one tenant-owned patient without owning the transaction."""

    def __init__(
        self,
        repository: PatientRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: CreatePatientCommand,
    ) -> CreatedPatient:
        """Validate, persist, and return a new patient record."""

        full_name = normalize_full_name(command.full_name)
        email = normalize_email(command.email)
        phone = normalize_phone(command.phone)
        external_reference = normalize_external_reference(command.external_reference)
        date_of_birth = validate_date_of_birth(command.date_of_birth)

        patient = Patient(
            tenant_id=command.tenant_id,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            phone=phone,
            external_reference=external_reference,
        )

        self._repository.add(patient)
        self._repository.flush()

        return CreatedPatient(
            patient=PatientRecord(
                id=patient.id,
                tenant_id=patient.tenant_id,
                full_name=patient.full_name,
                date_of_birth=patient.date_of_birth,
                email=patient.email,
                phone=patient.phone,
                external_reference=patient.external_reference,
                status=patient.status,
                version=patient.version,
                created_at=patient.created_at,
                updated_at=patient.updated_at,
            )
        )


__all__ = ["CreatePatientService"]
