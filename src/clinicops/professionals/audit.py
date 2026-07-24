from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import JSONObject, RecordAuditLogCommand
from clinicops.professionals.contracts import ProfessionalRecord
from clinicops.professionals.enums import (
    ProfessionalMutableField,
    ProfessionalStatus,
)


def professional_created_audit_command(
    *,
    professional: ProfessionalRecord,
    audit_context: AuditRecordingContext,
) -> RecordAuditLogCommand:
    """Build the safe audit fact for Professional creation."""

    return _command(
        professional=professional,
        audit_context=audit_context,
        action=AuditAction.PROFESSIONAL_CREATED,
        metadata={
            "status": professional.status.value,
            "version": professional.version,
        },
        idempotency_key=f"professional-created:{professional.id}",
    )


def professional_updated_audit_command(
    *,
    professional: ProfessionalRecord,
    changed_fields: tuple[ProfessionalMutableField, ...],
    audit_context: AuditRecordingContext,
) -> RecordAuditLogCommand:
    """Build the safe audit fact for a Professional profile update."""

    return _command(
        professional=professional,
        audit_context=audit_context,
        action=AuditAction.PROFESSIONAL_UPDATED,
        metadata={
            "version": professional.version,
            "changed_fields": [field.value for field in changed_fields],
        },
        idempotency_key=(f"professional-updated:{professional.id}:{professional.version}"),
    )


def professional_archived_audit_command(
    *,
    professional: ProfessionalRecord,
    audit_context: AuditRecordingContext,
) -> RecordAuditLogCommand:
    """Build the safe audit fact for Professional archival."""

    return _command(
        professional=professional,
        audit_context=audit_context,
        action=AuditAction.PROFESSIONAL_ARCHIVED,
        metadata={
            "previous_status": ProfessionalStatus.ACTIVE.value,
            "new_status": ProfessionalStatus.ARCHIVED.value,
            "version": professional.version,
        },
        idempotency_key=(f"professional-archived:{professional.id}:{professional.version}"),
    )


def professional_restored_audit_command(
    *,
    professional: ProfessionalRecord,
    audit_context: AuditRecordingContext,
) -> RecordAuditLogCommand:
    """Build the safe audit fact for Professional restoration."""

    return _command(
        professional=professional,
        audit_context=audit_context,
        action=AuditAction.PROFESSIONAL_RESTORED,
        metadata={
            "previous_status": ProfessionalStatus.ARCHIVED.value,
            "new_status": ProfessionalStatus.ACTIVE.value,
            "version": professional.version,
        },
        idempotency_key=(f"professional-restored:{professional.id}:{professional.version}"),
    )


def _command(
    *,
    professional: ProfessionalRecord,
    audit_context: AuditRecordingContext,
    action: AuditAction,
    metadata: JSONObject,
    idempotency_key: str,
) -> RecordAuditLogCommand:
    return RecordAuditLogCommand(
        tenant_id=professional.tenant_id,
        actor=audit_context.actor,
        source=audit_context.source,
        action=action.value,
        resource_type=AuditResourceType.PROFESSIONAL.value,
        resource_id=str(professional.id),
        correlation_id=audit_context.correlation_id,
        metadata_version=1,
        metadata=metadata,
        idempotency_key=idempotency_key,
        request_id=audit_context.request_id,
    )


__all__ = [
    "professional_archived_audit_command",
    "professional_created_audit_command",
    "professional_restored_audit_command",
    "professional_updated_audit_command",
]
