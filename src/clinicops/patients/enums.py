from enum import StrEnum


class PatientStatus(StrEnum):
    """Lifecycle status of a tenant-owned patient record."""

    ACTIVE = "active"
    ARCHIVED = "archived"
