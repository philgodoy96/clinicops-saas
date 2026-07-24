from dataclasses import dataclass
from uuid import UUID

from clinicops.patients.contracts import (
    ListPatientsCommand,
    PatientRecord,
)
from clinicops.patients.cursor import (
    decode_patient_cursor,
    encode_patient_cursor,
)
from clinicops.patients.enums import PatientListStatus
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.validation import normalize_patient_search

_MIN_PAGE_LIMIT = 1
_MAX_PAGE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ListPatientsQuery:
    """Application input for a tenant-scoped patient listing."""

    tenant_id: UUID
    limit: int = 50
    status: PatientListStatus = PatientListStatus.ACTIVE
    search: str | None = None
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ListedPatients:
    """Application result with an opaque next-page cursor."""

    items: tuple[PatientRecord, ...]
    next_cursor: str | None


class ListPatientsService:
    """List tenant-owned patients with bounded opaque pagination."""

    def __init__(
        self,
        repository: PatientRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        query: ListPatientsQuery,
    ) -> ListedPatients:
        """Normalize query input and return one patient page."""

        _validate_limit(query.limit)
        search = normalize_patient_search(query.search)
        cursor = decode_patient_cursor(query.cursor) if query.cursor is not None else None

        page = self._repository.list_page_for_tenant(
            ListPatientsCommand(
                tenant_id=query.tenant_id,
                limit=query.limit,
                status=query.status,
                search=search,
                cursor=cursor,
            )
        )

        next_cursor = (
            encode_patient_cursor(page.next_cursor) if page.next_cursor is not None else None
        )

        return ListedPatients(
            items=page.items,
            next_cursor=next_cursor,
        )


def _validate_limit(limit: int) -> None:
    if not _MIN_PAGE_LIMIT <= limit <= _MAX_PAGE_LIMIT:
        raise ValueError("Patient page limit must be between 1 and 100.")


__all__ = [
    "ListedPatients",
    "ListPatientsQuery",
    "ListPatientsService",
]
