from sqlalchemy.orm import Session

from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.professionals.audit import professional_created_audit_command
from clinicops.professionals.contracts import (
    CreatedProfessional,
    CreateProfessionalCommand,
    ProfessionalRecord,
)
from clinicops.professionals.models import Professional
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.validation import (
    normalize_email,
    normalize_external_reference,
    normalize_full_name,
    normalize_phone,
    normalize_registration_number,
    normalize_registration_region,
    normalize_specialty,
)


class CreateProfessionalService:
    """Create one tenant-owned professional without owning the transaction."""

    def __init__(
        self,
        repository: ProfessionalRepository,
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
        command: CreateProfessionalCommand,
    ) -> CreatedProfessional:
        """Validate, persist, and return a new professional profile."""

        full_name = normalize_full_name(command.full_name)
        specialty = normalize_specialty(command.specialty)
        registration_number = normalize_registration_number(command.registration_number)
        registration_region = normalize_registration_region(command.registration_region)
        email = normalize_email(command.email)
        phone = normalize_phone(command.phone)
        external_reference = normalize_external_reference(command.external_reference)

        professional = Professional(
            tenant_id=command.tenant_id,
            full_name=full_name,
            specialty=specialty,
            registration_number=registration_number,
            registration_region=registration_region,
            email=email,
            phone=phone,
            external_reference=external_reference,
        )

        self._repository.add(professional)
        self._repository.flush()

        professional_record = ProfessionalRecord(
            id=professional.id,
            tenant_id=professional.tenant_id,
            membership_id=professional.membership_id,
            full_name=professional.full_name,
            specialty=professional.specialty,
            registration_number=professional.registration_number,
            registration_region=professional.registration_region,
            email=professional.email,
            phone=professional.phone,
            external_reference=professional.external_reference,
            status=professional.status,
            version=professional.version,
            created_at=professional.created_at,
            updated_at=professional.updated_at,
        )

        self._audit_recorder.record(
            self._session,
            professional_created_audit_command(
                professional=professional_record,
                audit_context=command.audit_context,
            ),
        )

        return CreatedProfessional(professional=professional_record)


__all__ = ["CreateProfessionalService"]
