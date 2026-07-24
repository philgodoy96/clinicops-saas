from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.professionals.contracts import (
    ProfessionalCursor,
    ProfessionalPage,
    ProfessionalRecord,
)
from clinicops.professionals.cursor import encode_professional_cursor
from clinicops.professionals.enums import (
    ProfessionalListStatus,
    ProfessionalStatus,
)
from clinicops.professionals.exceptions import ProfessionalInvalidCursorError
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.list_professionals import (
    ListedProfessionals,
    ListProfessionalsQuery,
    ListProfessionalsService,
)

_RECORDED_AT = datetime(2026, 7, 24, 19, 30, tzinfo=UTC)


class RecordingProfessionalRepository:
    """Record tenant-scoped professional listing interactions."""

    def __init__(
        self,
        *,
        page: ProfessionalPage,
    ) -> None:
        self._page = page
        self.calls: list[
            tuple[
                UUID,
                int,
                ProfessionalListStatus,
                str | None,
                ProfessionalCursor | None,
            ]
        ] = []

    def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        limit: int,
        status: ProfessionalListStatus,
        search: str | None = None,
        cursor: ProfessionalCursor | None = None,
    ) -> ProfessionalPage:
        self.calls.append(
            (
                tenant_id,
                limit,
                status,
                search,
                cursor,
            )
        )
        return self._page


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _professional_record(
    *,
    tenant_id: UUID,
    professional_id: UUID | None = None,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=professional_id or uuid4(),
        tenant_id=tenant_id,
        membership_id=None,
        full_name="Morgan Reed",
        specialty="Dentistry",
        registration_number="DDS-48291",
        registration_region="CA",
        email="morgan@example.com",
        phone="+1-202-555-0130",
        external_reference="PROVIDER-100",
        status=status,
        version=1,
        created_at=_RECORDED_AT,
        updated_at=_RECORDED_AT,
    )


def test_list_professionals_uses_active_defaults() -> None:
    tenant_id = uuid4()
    professional = _professional_record(tenant_id=tenant_id)
    recording = RecordingProfessionalRepository(
        page=ProfessionalPage(
            items=(professional,),
            next_cursor=None,
        )
    )
    service = ListProfessionalsService(_repository(recording))

    result = service.execute(ListProfessionalsQuery(tenant_id=tenant_id))

    assert isinstance(result, ListedProfessionals)
    assert result.items == (professional,)
    assert result.next_cursor is None
    assert recording.calls == [
        (
            tenant_id,
            50,
            ProfessionalListStatus.ACTIVE,
            None,
            None,
        )
    ]


@pytest.mark.parametrize(
    "status",
    [
        ProfessionalListStatus.ARCHIVED,
        ProfessionalListStatus.ALL,
    ],
)
def test_list_professionals_propagates_status_filter(
    status: ProfessionalListStatus,
) -> None:
    tenant_id = uuid4()
    recording = RecordingProfessionalRepository(page=ProfessionalPage(items=(), next_cursor=None))
    service = ListProfessionalsService(_repository(recording))

    service.execute(
        ListProfessionalsQuery(
            tenant_id=tenant_id,
            status=status,
        )
    )

    assert recording.calls == [
        (
            tenant_id,
            50,
            status,
            None,
            None,
        )
    ]


def test_list_professionals_normalizes_search_before_repository_call() -> None:
    tenant_id = uuid4()
    recording = RecordingProfessionalRepository(page=ProfessionalPage(items=(), next_cursor=None))
    service = ListProfessionalsService(_repository(recording))

    service.execute(
        ListProfessionalsQuery(
            tenant_id=tenant_id,
            search="  Morgan  ",
        )
    )

    assert recording.calls == [
        (
            tenant_id,
            50,
            ProfessionalListStatus.ACTIVE,
            "Morgan",
            None,
        )
    ]


@pytest.mark.parametrize(
    "search",
    [
        None,
        "",
        "   ",
    ],
)
def test_list_professionals_normalizes_empty_search_to_none(
    search: str | None,
) -> None:
    tenant_id = uuid4()
    recording = RecordingProfessionalRepository(page=ProfessionalPage(items=(), next_cursor=None))
    service = ListProfessionalsService(_repository(recording))

    service.execute(
        ListProfessionalsQuery(
            tenant_id=tenant_id,
            search=search,
        )
    )

    assert recording.calls[0][3] is None


def test_list_professionals_decodes_input_and_encodes_next_cursor() -> None:
    tenant_id = uuid4()
    input_cursor = ProfessionalCursor(
        created_at=datetime(2026, 7, 24, 19, 0, tzinfo=UTC),
        professional_id=uuid4(),
    )
    next_cursor = ProfessionalCursor(
        created_at=datetime(2026, 7, 24, 18, 0, tzinfo=UTC),
        professional_id=uuid4(),
    )
    recording = RecordingProfessionalRepository(
        page=ProfessionalPage(
            items=(),
            next_cursor=next_cursor,
        )
    )
    service = ListProfessionalsService(_repository(recording))

    result = service.execute(
        ListProfessionalsQuery(
            tenant_id=tenant_id,
            limit=25,
            cursor=encode_professional_cursor(input_cursor),
        )
    )

    assert result.next_cursor == encode_professional_cursor(next_cursor)
    assert recording.calls == [
        (
            tenant_id,
            25,
            ProfessionalListStatus.ACTIVE,
            None,
            input_cursor,
        )
    ]


@pytest.mark.parametrize(
    "limit",
    [
        0,
        -1,
        101,
    ],
)
def test_list_professionals_rejects_out_of_range_limit_before_repository(
    limit: int,
) -> None:
    recording = RecordingProfessionalRepository(page=ProfessionalPage(items=(), next_cursor=None))
    service = ListProfessionalsService(_repository(recording))

    with pytest.raises(
        ValueError,
        match="Professional page limit must be between 1 and 100",
    ):
        service.execute(
            ListProfessionalsQuery(
                tenant_id=uuid4(),
                limit=limit,
            )
        )

    assert recording.calls == []


def test_list_professionals_accepts_limit_boundaries() -> None:
    tenant_id = uuid4()
    recording = RecordingProfessionalRepository(page=ProfessionalPage(items=(), next_cursor=None))
    service = ListProfessionalsService(_repository(recording))

    service.execute(
        ListProfessionalsQuery(
            tenant_id=tenant_id,
            limit=1,
        )
    )
    service.execute(
        ListProfessionalsQuery(
            tenant_id=tenant_id,
            limit=100,
        )
    )

    assert [call[1] for call in recording.calls] == [1, 100]


def test_list_professionals_rejects_invalid_cursor_before_repository() -> None:
    recording = RecordingProfessionalRepository(page=ProfessionalPage(items=(), next_cursor=None))
    service = ListProfessionalsService(_repository(recording))

    with pytest.raises(ProfessionalInvalidCursorError):
        service.execute(
            ListProfessionalsQuery(
                tenant_id=uuid4(),
                cursor="not-a-valid-cursor",
            )
        )

    assert recording.calls == []


def test_list_professionals_rejects_oversized_search_before_repository() -> None:
    recording = RecordingProfessionalRepository(page=ProfessionalPage(items=(), next_cursor=None))
    service = ListProfessionalsService(_repository(recording))

    with pytest.raises(ValueError, match="search must be at most 100"):
        service.execute(
            ListProfessionalsQuery(
                tenant_id=uuid4(),
                search="x" * 101,
            )
        )

    assert recording.calls == []


def test_list_professionals_propagates_repository_failure() -> None:
    class FailingProfessionalRepository:
        def list_for_tenant(
            self,
            *,
            tenant_id: UUID,
            limit: int,
            status: ProfessionalListStatus,
            search: str | None = None,
            cursor: ProfessionalCursor | None = None,
        ) -> ProfessionalPage:
            del tenant_id, limit, status, search, cursor
            raise RuntimeError("database unavailable")

    service = ListProfessionalsService(
        cast(ProfessionalRepository, FailingProfessionalRepository())
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.execute(ListProfessionalsQuery(tenant_id=uuid4()))
