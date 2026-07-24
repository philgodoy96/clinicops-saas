from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.patients.contracts import (
    CreatedPatient,
    CreatePatientCommand,
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
        session: Session,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._session = session
        self._audit_recorder = (
            audit_recorder if audit_recorder is not None else SqlAlchemyAuditRecorder()
        )

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

        audit_context = command.audit_context
        self._audit_recorder.record(
            self._session,
            RecordAuditLogCommand(
                tenant_id=command.tenant_id,
                actor=audit_context.actor,
                source=audit_context.source,
                action=AuditAction.PATIENT_CREATED.value,
                resource_type=AuditResourceType.PATIENT.value,
                resource_id=str(patient.id),
                correlation_id=audit_context.correlation_id,
                metadata_version=1,
                metadata={
                    "status": patient.status.value,
                    "version": patient.version,
                },
                idempotency_key=f"patient-created:{patient.id}",
                request_id=audit_context.request_id,
            ),
        )

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
