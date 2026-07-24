from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.validation import (
    EMAIL_MAX_LENGTH,
    EXTERNAL_REFERENCE_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
    PHONE_MAX_LENGTH,
    REGISTRATION_NUMBER_MAX_LENGTH,
    REGISTRATION_REGION_MAX_LENGTH,
    SPECIALTY_MAX_LENGTH,
)

ProfessionalFullName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=FULL_NAME_MAX_LENGTH,
    ),
]
ProfessionalSpecialty = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=SPECIALTY_MAX_LENGTH,
    ),
]
ProfessionalRegistrationNumber = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=REGISTRATION_NUMBER_MAX_LENGTH,
    ),
]
ProfessionalRegistrationRegion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=REGISTRATION_REGION_MAX_LENGTH,
    ),
]
ProfessionalEmail = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=EMAIL_MAX_LENGTH,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]
ProfessionalPhone = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=PHONE_MAX_LENGTH,
    ),
]
ProfessionalExternalReference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=EXTERNAL_REFERENCE_MAX_LENGTH,
    ),
]


class ProfessionalCreateRequest(BaseModel):
    """HTTP payload accepted when creating a Professional."""

    model_config = ConfigDict(extra="forbid")

    full_name: ProfessionalFullName
    specialty: ProfessionalSpecialty | None = None
    registration_number: ProfessionalRegistrationNumber | None = None
    registration_region: ProfessionalRegistrationRegion | None = None
    email: ProfessionalEmail | None = None
    phone: ProfessionalPhone | None = None
    external_reference: ProfessionalExternalReference | None = None


class ProfessionalUpdateRequest(BaseModel):
    """Partial Professional update at an observed resource version."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    full_name: ProfessionalFullName | None = None
    specialty: ProfessionalSpecialty | None = None
    registration_number: ProfessionalRegistrationNumber | None = None
    registration_region: ProfessionalRegistrationRegion | None = None
    email: ProfessionalEmail | None = None
    phone: ProfessionalPhone | None = None
    external_reference: ProfessionalExternalReference | None = None


class ProfessionalVersionRequest(BaseModel):
    """Observed Professional version required by a lifecycle mutation."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class ProfessionalMembershipLinkRequest(BaseModel):
    """Membership association and observed Professional version."""

    model_config = ConfigDict(extra="forbid")

    membership_id: UUID
    expected_version: int = Field(ge=1)


class ProfessionalResponse(BaseModel):
    """Public tenant-scoped Professional representation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

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


class ProfessionalListResponse(BaseModel):
    """One public page of tenant-scoped Professionals."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    items: list[ProfessionalResponse]
    next_cursor: str | None


__all__ = [
    "ProfessionalCreateRequest",
    "ProfessionalListResponse",
    "ProfessionalMembershipLinkRequest",
    "ProfessionalResponse",
    "ProfessionalUpdateRequest",
    "ProfessionalVersionRequest",
]
