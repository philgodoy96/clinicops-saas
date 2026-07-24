from dataclasses import dataclass
from uuid import UUID

from clinicops.professionals.contracts import ProfessionalRecord
from clinicops.professionals.cursor import (
    decode_professional_cursor,
    encode_professional_cursor,
)
from clinicops.professionals.enums import ProfessionalListStatus
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.validation import normalize_professional_search

_MIN_PAGE_LIMIT = 1
_MAX_PAGE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ListProfessionalsQuery:
    """Application input for a tenant-scoped professional listing."""

    tenant_id: UUID
    limit: int = 50
    status: ProfessionalListStatus = ProfessionalListStatus.ACTIVE
    search: str | None = None
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ListedProfessionals:
    """Application result with an opaque next-page cursor."""

    items: tuple[ProfessionalRecord, ...]
    next_cursor: str | None


class ListProfessionalsService:
    """List tenant-owned professionals with bounded opaque pagination."""

    def __init__(
        self,
        repository: ProfessionalRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        query: ListProfessionalsQuery,
    ) -> ListedProfessionals:
        """Normalize query input and return one professional page."""

        _validate_limit(query.limit)
        search = normalize_professional_search(query.search)
        cursor = decode_professional_cursor(query.cursor) if query.cursor is not None else None

        page = self._repository.list_for_tenant(
            tenant_id=query.tenant_id,
            limit=query.limit,
            status=query.status,
            search=search,
            cursor=cursor,
        )

        next_cursor = (
            encode_professional_cursor(page.next_cursor) if page.next_cursor is not None else None
        )

        return ListedProfessionals(
            items=page.items,
            next_cursor=next_cursor,
        )


def _validate_limit(limit: int) -> None:
    if not _MIN_PAGE_LIMIT <= limit <= _MAX_PAGE_LIMIT:
        raise ValueError("Professional page limit must be between 1 and 100.")


__all__ = [
    "ListProfessionalsQuery",
    "ListProfessionalsService",
    "ListedProfessionals",
]
