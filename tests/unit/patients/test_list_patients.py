from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.patients.contracts import (
    ListPatientsCommand,
    PatientCursor,
    PatientPage,
    PatientRecord,
)
from clinicops.patients.cursor import encode_patient_cursor
from clinicops.patients.enums import (
    PatientListStatus,
    PatientStatus,
)
from clinicops.patients.exceptions import PatientInvalidCursorError
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.list_patients import (
    ListedPatients,
    ListPatientsQuery,
    ListPatientsService,
)
from clinicops.patients.validation import SEARCH_MAX_LENGTH


class RecordingPatientRepository:
    def __init__(
        self,
        *,
        page: PatientPage,
    ) -> None:
        self.page = page
        self.queries: list[ListPatientsCommand] = []

    def list_page_for_tenant(
        self,
        query: ListPatientsCommand,
    ) -> PatientPage:
        self.queries.append(query)
        return self.page


def _patient_record(
    *,
    tenant_id: UUID,
    status: PatientStatus = PatientStatus.ACTIVE,
) -> PatientRecord:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    return PatientRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        full_name="Jordan Lee",
        date_of_birth=None,
        email=None,
        phone=None,
        external_reference=None,
        status=status,
        version=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _service(
    repository: RecordingPatientRepository,
) -> ListPatientsService:
    return ListPatientsService(cast(PatientRepository, repository))


def test_list_patients_uses_approved_defaults() -> None:
    tenant_id = uuid4()
    repository = RecordingPatientRepository(page=PatientPage(items=(), next_cursor=None))
    service = _service(repository)

    result = service.execute(ListPatientsQuery(tenant_id=tenant_id))

    assert result == ListedPatients(
        items=(),
        next_cursor=None,
    )
    assert repository.queries == [
        ListPatientsCommand(
            tenant_id=tenant_id,
            limit=50,
            status=PatientListStatus.ACTIVE,
            search=None,
            cursor=None,
        )
    ]


@pytest.mark.parametrize(
    "status",
    [
        PatientListStatus.ACTIVE,
        PatientListStatus.ARCHIVED,
        PatientListStatus.ALL,
    ],
)
def test_list_patients_propagates_status_filter(
    status: PatientListStatus,
) -> None:
    tenant_id = uuid4()
    repository = RecordingPatientRepository(page=PatientPage(items=(), next_cursor=None))
    service = _service(repository)

    service.execute(
        ListPatientsQuery(
            tenant_id=tenant_id,
            status=status,
        )
    )

    assert repository.queries[0].status is status


def test_list_patients_normalizes_search_before_repository_call() -> None:
    tenant_id = uuid4()
    repository = RecordingPatientRepository(page=PatientPage(items=(), next_cursor=None))
    service = _service(repository)

    service.execute(
        ListPatientsQuery(
            tenant_id=tenant_id,
            search="  Jordan Lee  ",
        )
    )

    assert repository.queries[0].search == "Jordan Lee"


def test_list_patients_converts_blank_search_to_none() -> None:
    repository = RecordingPatientRepository(page=PatientPage(items=(), next_cursor=None))
    service = _service(repository)

    service.execute(
        ListPatientsQuery(
            tenant_id=uuid4(),
            search="   ",
        )
    )

    assert repository.queries[0].search is None


def test_list_patients_decodes_opaque_cursor() -> None:
    tenant_id = uuid4()
    cursor = PatientCursor(
        created_at=datetime(
            2026,
            7,
            23,
            21,
            30,
            tzinfo=UTC,
        ),
        patient_id=uuid4(),
    )
    repository = RecordingPatientRepository(page=PatientPage(items=(), next_cursor=None))
    service = _service(repository)

    service.execute(
        ListPatientsQuery(
            tenant_id=tenant_id,
            cursor=encode_patient_cursor(cursor),
        )
    )

    assert repository.queries[0].cursor == cursor


def test_list_patients_encodes_next_cursor() -> None:
    tenant_id = uuid4()
    patient = _patient_record(tenant_id=tenant_id)
    next_cursor = PatientCursor(
        created_at=patient.created_at,
        patient_id=patient.id,
    )
    repository = RecordingPatientRepository(
        page=PatientPage(
            items=(patient,),
            next_cursor=next_cursor,
        )
    )
    service = _service(repository)

    result = service.execute(ListPatientsQuery(tenant_id=tenant_id))

    assert result.items == (patient,)
    assert result.next_cursor == encode_patient_cursor(next_cursor)


@pytest.mark.parametrize("limit", [0, 101])
def test_list_patients_rejects_invalid_limit_before_repository_call(
    limit: int,
) -> None:
    repository = RecordingPatientRepository(page=PatientPage(items=(), next_cursor=None))
    service = _service(repository)

    with pytest.raises(ValueError, match="between 1 and 100"):
        service.execute(
            ListPatientsQuery(
                tenant_id=uuid4(),
                limit=limit,
            )
        )

    assert repository.queries == []


def test_list_patients_rejects_long_search_before_repository_call() -> None:
    repository = RecordingPatientRepository(page=PatientPage(items=(), next_cursor=None))
    service = _service(repository)

    with pytest.raises(ValueError, match=str(SEARCH_MAX_LENGTH)):
        service.execute(
            ListPatientsQuery(
                tenant_id=uuid4(),
                search="s" * (SEARCH_MAX_LENGTH + 1),
            )
        )

    assert repository.queries == []


def test_list_patients_rejects_invalid_cursor_before_repository_call() -> None:
    repository = RecordingPatientRepository(page=PatientPage(items=(), next_cursor=None))
    service = _service(repository)

    with pytest.raises(PatientInvalidCursorError):
        service.execute(
            ListPatientsQuery(
                tenant_id=uuid4(),
                cursor="invalid-cursor!",
            )
        )

    assert repository.queries == []
