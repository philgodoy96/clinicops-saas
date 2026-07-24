from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.audit.context import AuditRecordingContext
from clinicops.professionals.contracts import (
    ArchiveProfessionalCommand,
    CreateProfessionalCommand,
    LinkProfessionalMembershipCommand,
    ListProfessionalsCommand,
    ProfessionalCursor,
    ProfessionalPage,
    ProfessionalRecord,
    RestoreProfessionalCommand,
    UnlinkProfessionalForMembershipRemovalCommand,
    UnlinkProfessionalMembershipCommand,
    UpdatedProfessional,
    UpdateProfessionalCommand,
)
from clinicops.professionals.enums import (
    ProfessionalListStatus,
    ProfessionalMutableField,
    ProfessionalStatus,
)


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=uuid4(),
        role="admin",
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
    )


def _professional_record() -> ProfessionalRecord:
    timestamp = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
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
        status=ProfessionalStatus.ACTIVE,
        version=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_professional_record_is_immutable() -> None:
    professional = _professional_record()

    with pytest.raises(FrozenInstanceError):
        professional.full_name = "Changed"  # type: ignore[misc]


def test_create_command_supports_unlinked_professional_profile() -> None:
    tenant_id = uuid4()

    command = CreateProfessionalCommand(
        tenant_id=tenant_id,
        full_name="Morgan Reed",
        audit_context=_audit_context(),
    )

    assert command.tenant_id == tenant_id
    assert command.specialty is None
    assert command.registration_number is None
    assert command.registration_region is None
    assert command.email is None
    assert command.phone is None
    assert command.external_reference is None


def test_list_command_uses_active_default_and_no_cursor() -> None:
    command = ListProfessionalsCommand(tenant_id=uuid4())

    assert command.limit == 50
    assert command.status is ProfessionalListStatus.ACTIVE
    assert command.search is None
    assert command.cursor is None


def test_professional_page_preserves_stable_cursor_position() -> None:
    professional = _professional_record()
    cursor = ProfessionalCursor(
        created_at=professional.created_at,
        professional_id=professional.id,
    )

    page = ProfessionalPage(
        items=(professional,),
        next_cursor=cursor,
    )

    assert page.items == (professional,)
    assert page.next_cursor == cursor


def test_update_command_distinguishes_explicit_null_from_omission() -> None:
    command = UpdateProfessionalCommand(
        tenant_id=uuid4(),
        professional_id=uuid4(),
        expected_version=3,
        fields_to_update=frozenset({ProfessionalMutableField.EMAIL}),
        audit_context=_audit_context(),
        email=None,
    )

    assert command.fields_to_update == frozenset({ProfessionalMutableField.EMAIL})
    assert command.email is None
    assert command.specialty is None


def test_mutation_commands_preserve_supplied_audit_context() -> None:
    context = _audit_context()
    tenant_id = uuid4()
    professional_id = uuid4()

    create = CreateProfessionalCommand(
        tenant_id=tenant_id,
        full_name="Morgan Reed",
        audit_context=context,
    )
    update = UpdateProfessionalCommand(
        tenant_id=tenant_id,
        professional_id=professional_id,
        expected_version=3,
        fields_to_update=frozenset({ProfessionalMutableField.EMAIL}),
        audit_context=context,
        email=None,
    )
    archive = ArchiveProfessionalCommand(
        tenant_id=tenant_id,
        professional_id=professional_id,
        expected_version=3,
        audit_context=context,
    )
    restore = RestoreProfessionalCommand(
        tenant_id=tenant_id,
        professional_id=professional_id,
        expected_version=3,
        audit_context=context,
    )
    link = LinkProfessionalMembershipCommand(
        tenant_id=tenant_id,
        professional_id=professional_id,
        membership_id=uuid4(),
        expected_version=3,
        audit_context=context,
    )
    unlink = UnlinkProfessionalMembershipCommand(
        tenant_id=tenant_id,
        professional_id=professional_id,
        expected_version=3,
        audit_context=context,
    )

    assert create.audit_context is context
    assert update.audit_context is context
    assert archive.audit_context is context
    assert restore.audit_context is context
    assert link.audit_context is context
    assert unlink.audit_context is context

    with pytest.raises(FrozenInstanceError):
        create.audit_context = _audit_context()  # type: ignore[misc]


def test_updated_result_preserves_deterministic_changed_fields() -> None:
    professional = _professional_record()
    result = UpdatedProfessional(
        professional=professional,
        changed_fields=(
            ProfessionalMutableField.SPECIALTY,
            ProfessionalMutableField.EMAIL,
        ),
    )

    assert result.professional == professional
    assert result.changed_fields == (
        ProfessionalMutableField.SPECIALTY,
        ProfessionalMutableField.EMAIL,
    )


def test_link_command_carries_tenant_professional_membership_and_version() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    membership_id = uuid4()
    context = _audit_context()

    command = LinkProfessionalMembershipCommand(
        tenant_id=tenant_id,
        professional_id=professional_id,
        membership_id=membership_id,
        expected_version=2,
        audit_context=context,
    )

    assert command.tenant_id == tenant_id
    assert command.professional_id == professional_id
    assert command.membership_id == membership_id
    assert command.expected_version == 2
    assert command.audit_context is context


def test_explicit_unlink_command_does_not_accept_membership_identifier() -> None:
    context = _audit_context()

    command = UnlinkProfessionalMembershipCommand(
        tenant_id=uuid4(),
        professional_id=uuid4(),
        expected_version=4,
        audit_context=context,
    )

    assert not hasattr(command, "membership_id")
    assert command.audit_context is context


def test_membership_removal_unlink_command_is_tenant_scoped() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    context = _audit_context()

    command = UnlinkProfessionalForMembershipRemovalCommand(
        tenant_id=tenant_id,
        membership_id=membership_id,
        audit_context=context,
    )

    assert command.tenant_id == tenant_id
    assert command.membership_id == membership_id
    assert command.audit_context is context
    assert not hasattr(command, "professional_id")
    assert not hasattr(command, "expected_version")
