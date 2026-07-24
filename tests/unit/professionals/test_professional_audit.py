from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.enums import AuditSource
from clinicops.professionals.audit import (
    professional_archived_audit_command,
    professional_created_audit_command,
    professional_membership_linked_audit_command,
    professional_membership_unlinked_audit_command,
    professional_restored_audit_command,
    professional_updated_audit_command,
)
from clinicops.professionals.contracts import ProfessionalRecord
from clinicops.professionals.enums import (
    ProfessionalMutableField,
    ProfessionalStatus,
)

_RECORDED_AT = datetime(2026, 7, 24, 23, 30, tzinfo=UTC)
_FORBIDDEN_METADATA_KEYS = {
    "full_name",
    "specialty",
    "registration_number",
    "registration_region",
    "email",
    "phone",
    "external_reference",
    "tenant_id",
}


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=uuid4(),
        role="admin",
        request_id="request-professional-audit",
        correlation_id="correlation-professional-audit",
    )


def _professional(
    *,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    version: int = 1,
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=uuid4(),
        tenant_id=uuid4(),
        membership_id=None,
        full_name="Morgan Reed",
        specialty="Dentistry",
        registration_number="DDS-48291",
        registration_region="CA",
        email="morgan@example.com",
        phone="+1-202-555-0130",
        external_reference="PROVIDER-100",
        status=status,
        version=version,
        created_at=_RECORDED_AT,
        updated_at=_RECORDED_AT,
    )


@pytest.mark.parametrize(
    "builder",
    [
        lambda professional, context: professional_created_audit_command(
            professional=professional,
            audit_context=context,
        ),
        lambda professional, context: professional_updated_audit_command(
            professional=professional,
            changed_fields=(
                ProfessionalMutableField.FULL_NAME,
                ProfessionalMutableField.EMAIL,
            ),
            audit_context=context,
        ),
        lambda professional, context: professional_archived_audit_command(
            professional=professional,
            audit_context=context,
        ),
        lambda professional, context: professional_restored_audit_command(
            professional=professional,
            audit_context=context,
        ),
    ],
)
def test_professional_audit_commands_share_safe_common_attribution(
    builder: Callable[
        [ProfessionalRecord, AuditRecordingContext],
        RecordAuditLogCommand,
    ],
) -> None:
    professional = _professional(version=2)
    context = _audit_context()

    command = builder(professional, context)

    assert command.tenant_id == professional.tenant_id
    assert command.resource_type == AuditResourceType.PROFESSIONAL.value
    assert command.resource_id == str(professional.id)
    assert command.actor == context.actor
    assert command.source is AuditSource.HTTP
    assert command.request_id == context.request_id
    assert command.correlation_id == context.correlation_id
    assert command.metadata_version == 1
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(command.metadata)


def test_professional_created_audit_shape() -> None:
    professional = _professional()
    command = professional_created_audit_command(
        professional=professional,
        audit_context=_audit_context(),
    )

    assert command.action == AuditAction.PROFESSIONAL_CREATED.value
    assert command.metadata == {
        "status": "active",
        "version": 1,
    }
    assert command.idempotency_key == (f"professional-created:{professional.id}")


def test_professional_updated_audit_shape() -> None:
    professional = _professional(version=2)
    command = professional_updated_audit_command(
        professional=professional,
        changed_fields=(
            ProfessionalMutableField.FULL_NAME,
            ProfessionalMutableField.EMAIL,
        ),
        audit_context=_audit_context(),
    )

    assert command.action == AuditAction.PROFESSIONAL_UPDATED.value
    assert command.metadata == {
        "version": 2,
        "changed_fields": ["full_name", "email"],
    }
    assert command.idempotency_key == (f"professional-updated:{professional.id}:2")


def test_professional_archive_and_restore_audit_shapes() -> None:
    archived = _professional(
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    restored = _professional(
        status=ProfessionalStatus.ACTIVE,
        version=3,
    )

    archive_command = professional_archived_audit_command(
        professional=archived,
        audit_context=_audit_context(),
    )
    restore_command = professional_restored_audit_command(
        professional=restored,
        audit_context=_audit_context(),
    )

    assert archive_command.action == AuditAction.PROFESSIONAL_ARCHIVED.value
    assert archive_command.metadata == {
        "previous_status": "active",
        "new_status": "archived",
        "version": 2,
    }
    assert archive_command.idempotency_key == (f"professional-archived:{archived.id}:2")

    assert restore_command.action == AuditAction.PROFESSIONAL_RESTORED.value
    assert restore_command.metadata == {
        "previous_status": "archived",
        "new_status": "active",
        "version": 3,
    }
    assert restore_command.idempotency_key == (f"professional-restored:{restored.id}:3")


_PII_VALUES = {
    "Morgan Reed",
    "Dentistry",
    "DDS-48291",
    "CA",
    "morgan@example.com",
    "+1-202-555-0130",
    "PROVIDER-100",
}
_FORBIDDEN_MEMBERSHIP_KEYS = {"role", "status", "user_id"}


def _assert_safe_membership_audit_surface(
    command: RecordAuditLogCommand,
    *,
    professional: ProfessionalRecord,
    context: AuditRecordingContext,
) -> None:
    assert command.tenant_id == professional.tenant_id
    assert command.resource_type == AuditResourceType.PROFESSIONAL.value
    assert command.resource_id == str(professional.id)
    assert command.actor == context.actor
    assert command.source is AuditSource.HTTP
    assert command.request_id == context.request_id
    assert command.correlation_id == context.correlation_id
    assert command.metadata_version == 1
    assert _FORBIDDEN_METADATA_KEYS.isdisjoint(command.metadata)
    assert _FORBIDDEN_MEMBERSHIP_KEYS.isdisjoint(command.metadata)
    assert _PII_VALUES.isdisjoint({str(value) for value in command.metadata.values()})


def test_professional_membership_linked_audit_shape() -> None:
    professional = _professional(version=2)
    membership_id = uuid4()
    context = _audit_context()

    command = professional_membership_linked_audit_command(
        professional=professional,
        membership_id=membership_id,
        audit_context=context,
    )

    _assert_safe_membership_audit_surface(
        command,
        professional=professional,
        context=context,
    )
    assert command.action == AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value
    assert command.metadata == {
        "membership_id": str(membership_id),
        "version": professional.version,
    }
    assert command.idempotency_key == (
        f"professional-membership-linked:{professional.id}:{professional.version}"
    )


def test_professional_membership_unlinked_audit_shape() -> None:
    professional = _professional(version=3)
    membership_id = uuid4()
    context = _audit_context()

    command = professional_membership_unlinked_audit_command(
        professional=professional,
        membership_id=membership_id,
        reason="explicit",
        audit_context=context,
    )

    _assert_safe_membership_audit_surface(
        command,
        professional=professional,
        context=context,
    )
    assert command.action == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
    assert command.metadata == {
        "membership_id": str(membership_id),
        "reason": "explicit",
        "version": professional.version,
    }
    assert command.idempotency_key == (
        f"professional-membership-unlinked:{professional.id}:{professional.version}"
    )


def test_professional_membership_unlinked_membership_removal_audit_shape() -> None:
    professional = _professional(version=4)
    membership_id = uuid4()
    context = _audit_context()

    command = professional_membership_unlinked_audit_command(
        professional=professional,
        membership_id=membership_id,
        reason="membership_removal",
        audit_context=context,
    )

    _assert_safe_membership_audit_surface(
        command,
        professional=professional,
        context=context,
    )
    assert command.action == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
    assert command.metadata == {
        "membership_id": str(membership_id),
        "reason": "membership_removal",
        "version": professional.version,
    }
    assert command.idempotency_key == (
        f"professional-membership-unlinked:{professional.id}:{professional.version}"
    )
