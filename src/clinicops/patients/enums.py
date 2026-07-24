from enum import StrEnum


class PatientStatus(StrEnum):
    """Lifecycle status of a tenant-owned patient record."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class PatientListStatus(StrEnum):
    """Application-level status filter for patient listings."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    ALL = "all"


class PatientMutableField(StrEnum):
    """Patient profile fields accepted by partial updates."""

    FULL_NAME = "full_name"
    DATE_OF_BIRTH = "date_of_birth"
    EMAIL = "email"
    PHONE = "phone"
    EXTERNAL_REFERENCE = "external_reference"


__all__ = [
    "PatientListStatus",
    "PatientMutableField",
    "PatientStatus",
]
