from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from clinicops.patients.enums import PatientStatus
from clinicops.patients.validation import (
    EMAIL_MAX_LENGTH,
    EXTERNAL_REFERENCE_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
    PHONE_MAX_LENGTH,
)

PatientFullName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=FULL_NAME_MAX_LENGTH,
    ),
]
PatientEmail = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=EMAIL_MAX_LENGTH,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]
PatientPhone = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=PHONE_MAX_LENGTH,
    ),
]
PatientExternalReference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=EXTERNAL_REFERENCE_MAX_LENGTH,
    ),
]


class PatientCreateRequest(BaseModel):
    """HTTP payload accepted when creating a patient."""

    model_config = ConfigDict(extra="forbid")

    full_name: PatientFullName
    date_of_birth: date | None = None
    email: PatientEmail | None = None
    phone: PatientPhone | None = None
    external_reference: PatientExternalReference | None = None


class PatientUpdateRequest(BaseModel):
    """Partial patient update with an observed resource version."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    full_name: PatientFullName | None = None
    date_of_birth: date | None = None
    email: PatientEmail | None = None
    phone: PatientPhone | None = None
    external_reference: PatientExternalReference | None = None


class PatientVersionRequest(BaseModel):
    """Observed patient version required by lifecycle mutations."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class PatientResponse(BaseModel):
    """Public tenant-scoped patient representation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    id: UUID
    tenant_id: UUID
    full_name: str
    date_of_birth: date | None
    email: str | None
    phone: str | None
    external_reference: str | None
    status: PatientStatus
    version: int
    created_at: datetime
    updated_at: datetime


class PatientListResponse(BaseModel):
    """One public page of tenant-scoped patients."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    items: list[PatientResponse]
    next_cursor: str | None


__all__ = [
    "PatientCreateRequest",
    "PatientListResponse",
    "PatientResponse",
    "PatientUpdateRequest",
    "PatientVersionRequest",
]
