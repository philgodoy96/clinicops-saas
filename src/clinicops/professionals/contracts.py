from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from clinicops.professionals.enums import (
    ProfessionalListStatus,
    ProfessionalMutableField,
    ProfessionalStatus,
)


@dataclass(frozen=True, slots=True)
class ProfessionalRecord:
    """Immutable representation of a tenant-owned professional profile."""

    id: UUID
    tenant_id: UUID
    membership_id: UUID | None
    full_name: str
    specialty: str | None
    registration_number: str | None
    registration_region: str | None
    email: str | None
    phone: str | None
    external_reference: str | None
    status: ProfessionalStatus
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProfessionalCursor:
    """Stable keyset position for descending professional pagination."""

    created_at: datetime
    professional_id: UUID


@dataclass(frozen=True, slots=True)
class ProfessionalPage:
    """One immutable page of tenant-scoped professional records."""

    items: tuple[ProfessionalRecord, ...]
    next_cursor: ProfessionalCursor | None


@dataclass(frozen=True, slots=True)
class CreateProfessionalCommand:
    """Input required to create a professional within one tenant."""

    tenant_id: UUID
    full_name: str
    specialty: str | None = None
    registration_number: str | None = None
    registration_region: str | None = None
    email: str | None = None
    phone: str | None = None
    external_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedProfessional:
    """Result of a successful professional creation."""

    professional: ProfessionalRecord


@dataclass(frozen=True, slots=True)
class GetProfessionalCommand:
    """Input required to retrieve one tenant-owned professional."""

    tenant_id: UUID
    professional_id: UUID


@dataclass(frozen=True, slots=True)
class ListProfessionalsCommand:
    """Input required to list professionals within one tenant."""

    tenant_id: UUID
    limit: int = 50
    status: ProfessionalListStatus = ProfessionalListStatus.ACTIVE
    search: str | None = None
    cursor: ProfessionalCursor | None = None


@dataclass(frozen=True, slots=True)
class UpdateProfessionalCommand:
    """Input required for a versioned partial professional update."""

    tenant_id: UUID
    professional_id: UUID
    expected_version: int
    fields_to_update: frozenset[ProfessionalMutableField]
    full_name: str | None = None
    specialty: str | None = None
    registration_number: str | None = None
    registration_region: str | None = None
    email: str | None = None
    phone: str | None = None
    external_reference: str | None = None


@dataclass(frozen=True, slots=True)
class UpdatedProfessional:
    """Result of a successful versioned professional update."""

    professional: ProfessionalRecord
    changed_fields: tuple[ProfessionalMutableField, ...]


@dataclass(frozen=True, slots=True)
class ArchiveProfessionalCommand:
    """Input required to archive an active professional."""

    tenant_id: UUID
    professional_id: UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class ArchivedProfessional:
    """Result of a successful professional archival."""

    professional: ProfessionalRecord


@dataclass(frozen=True, slots=True)
class RestoreProfessionalCommand:
    """Input required to restore an archived professional."""

    tenant_id: UUID
    professional_id: UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class RestoredProfessional:
    """Result of a successful professional restoration."""

    professional: ProfessionalRecord


@dataclass(frozen=True, slots=True)
class LinkProfessionalMembershipCommand:
    """Input required to link one active professional to a membership."""

    tenant_id: UUID
    professional_id: UUID
    membership_id: UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class LinkedProfessionalMembership:
    """Result of a successful professional-membership link."""

    professional: ProfessionalRecord


@dataclass(frozen=True, slots=True)
class UnlinkProfessionalMembershipCommand:
    """Input required to unlink one active professional from its membership."""

    tenant_id: UUID
    professional_id: UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class UnlinkedProfessionalMembership:
    """Result of a successful explicit professional-membership unlink."""

    professional: ProfessionalRecord
    previous_membership_id: UUID


@dataclass(frozen=True, slots=True)
class UnlinkProfessionalForMembershipRemovalCommand:
    """Input used when Membership removal must clear a professional link."""

    tenant_id: UUID
    membership_id: UUID


@dataclass(frozen=True, slots=True)
class UnlinkedProfessionalForMembershipRemoval:
    """Result of clearing a professional link during Membership removal."""

    professional: ProfessionalRecord | None
    previous_membership_id: UUID | None


__all__ = [
    "ArchiveProfessionalCommand",
    "ArchivedProfessional",
    "CreateProfessionalCommand",
    "CreatedProfessional",
    "GetProfessionalCommand",
    "LinkProfessionalMembershipCommand",
    "LinkedProfessionalMembership",
    "ListProfessionalsCommand",
    "ProfessionalCursor",
    "ProfessionalPage",
    "ProfessionalRecord",
    "RestoreProfessionalCommand",
    "RestoredProfessional",
    "UnlinkProfessionalForMembershipRemovalCommand",
    "UnlinkProfessionalMembershipCommand",
    "UnlinkedProfessionalForMembershipRemoval",
    "UnlinkedProfessionalMembership",
    "UpdateProfessionalCommand",
    "UpdatedProfessional",
]
