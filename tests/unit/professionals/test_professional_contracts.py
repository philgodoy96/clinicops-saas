from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.professionals.contracts import (
    CreateProfessionalCommand,
    LinkProfessionalMembershipCommand,
    ListProfessionalsCommand,
    ProfessionalCursor,
    ProfessionalPage,
    ProfessionalRecord,
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
        email=None,
    )

    assert command.fields_to_update == frozenset({ProfessionalMutableField.EMAIL})
    assert command.email is None
    assert command.specialty is None


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

    command = LinkProfessionalMembershipCommand(
        tenant_id=tenant_id,
        professional_id=professional_id,
        membership_id=membership_id,
        expected_version=2,
    )

    assert command.tenant_id == tenant_id
    assert command.professional_id == professional_id
    assert command.membership_id == membership_id
    assert command.expected_version == 2


def test_explicit_unlink_command_does_not_accept_membership_identifier() -> None:
    command = UnlinkProfessionalMembershipCommand(
        tenant_id=uuid4(),
        professional_id=uuid4(),
        expected_version=4,
    )

    assert not hasattr(command, "membership_id")


def test_membership_removal_unlink_command_is_tenant_scoped() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()

    command = UnlinkProfessionalForMembershipRemovalCommand(
        tenant_id=tenant_id,
        membership_id=membership_id,
    )

    assert command.tenant_id == tenant_id
    assert command.membership_id == membership_id
    assert not hasattr(command, "professional_id")
    assert not hasattr(command, "expected_version")
