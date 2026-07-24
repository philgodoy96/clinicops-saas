from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from clinicops.patients.enums import (
    PatientListStatus,
    PatientMutableField,
    PatientStatus,
)


@dataclass(frozen=True, slots=True)
class PatientRecord:
    """Immutable representation of a tenant-owned patient record."""

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


@dataclass(frozen=True, slots=True)
class PatientCursor:
    """Stable keyset position for descending patient pagination."""

    created_at: datetime
    patient_id: UUID


@dataclass(frozen=True, slots=True)
class PatientPage:
    """One immutable page of tenant-scoped patient records."""

    items: tuple[PatientRecord, ...]
    next_cursor: PatientCursor | None


@dataclass(frozen=True, slots=True)
class CreatePatientCommand:
    """Input required to create a patient within one tenant."""

    tenant_id: UUID
    full_name: str
    date_of_birth: date | None = None
    email: str | None = None
    phone: str | None = None
    external_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedPatient:
    """Result of a successful patient creation."""

    patient: PatientRecord


@dataclass(frozen=True, slots=True)
class GetPatientCommand:
    """Input required to retrieve one tenant-owned patient."""

    tenant_id: UUID
    patient_id: UUID


@dataclass(frozen=True, slots=True)
class ListPatientsCommand:
    """Input required to list patients within one tenant."""

    tenant_id: UUID
    limit: int = 50
    status: PatientListStatus = PatientListStatus.ACTIVE
    search: str | None = None
    cursor: PatientCursor | None = None


@dataclass(frozen=True, slots=True)
class UpdatePatientCommand:
    """Input required for a versioned partial patient update."""

    tenant_id: UUID
    patient_id: UUID
    expected_version: int
    fields_to_update: frozenset[PatientMutableField]
    full_name: str | None = None
    date_of_birth: date | None = None
    email: str | None = None
    phone: str | None = None
    external_reference: str | None = None


@dataclass(frozen=True, slots=True)
class UpdatedPatient:
    """Result of a successful versioned patient update."""

    patient: PatientRecord
    changed_fields: tuple[PatientMutableField, ...]


@dataclass(frozen=True, slots=True)
class ArchivePatientCommand:
    """Input required to archive an active patient."""

    tenant_id: UUID
    patient_id: UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class ArchivedPatient:
    """Result of a successful patient archival."""

    patient: PatientRecord


@dataclass(frozen=True, slots=True)
class RestorePatientCommand:
    """Input required to restore an archived patient."""

    tenant_id: UUID
    patient_id: UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class RestoredPatient:
    """Result of a successful patient restoration."""

    patient: PatientRecord


__all__ = [
    "ArchivePatientCommand",
    "ArchivedPatient",
    "CreatePatientCommand",
    "CreatedPatient",
    "GetPatientCommand",
    "ListPatientsCommand",
    "PatientCursor",
    "PatientPage",
    "PatientRecord",
    "RestorePatientCommand",
    "RestoredPatient",
    "UpdatePatientCommand",
    "UpdatedPatient",
]
