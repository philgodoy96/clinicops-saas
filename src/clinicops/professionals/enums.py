from enum import StrEnum


class ProfessionalStatus(StrEnum):
    """Lifecycle status of a tenant-owned professional profile."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ProfessionalListStatus(StrEnum):
    """Application-level status filter for professional listings."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    ALL = "all"


class ProfessionalMutableField(StrEnum):
    """Professional profile fields accepted by partial updates."""

    FULL_NAME = "full_name"
    SPECIALTY = "specialty"
    REGISTRATION_NUMBER = "registration_number"
    REGISTRATION_REGION = "registration_region"
    EMAIL = "email"
    PHONE = "phone"
    EXTERNAL_REFERENCE = "external_reference"


__all__ = [
    "ProfessionalListStatus",
    "ProfessionalMutableField",
    "ProfessionalStatus",
]
