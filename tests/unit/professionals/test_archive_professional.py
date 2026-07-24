from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.professionals.contracts import (
    ArchiveProfessionalCommand,
    ProfessionalRecord,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalAlreadyArchivedError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.archive_professional import (
    ArchiveProfessionalService,
)

_RECORDED_AT = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)


class RecordingProfessionalRepository:
    """Record optimistic professional archive interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        archived: ProfessionalRecord | None = None,
        archive_error: Exception | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._archived = archived
        self._archive_error = archive_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.archive_calls: list[tuple[UUID, UUID, int]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def archive_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        self.archive_calls.append((tenant_id, professional_id, expected_version))
        if self._archive_error is not None:
            raise self._archive_error
        return self._archived


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _professional_record(
    *,
    tenant_id: UUID | None = None,
    professional_id: UUID | None = None,
    membership_id: UUID | None = None,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    version: int = 1,
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=professional_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        membership_id=membership_id,
        full_name="Morgan Reed",
        specialty="Dentistry",
        registration_number="DDS-48291",
        registration_region="CA",
        email="morgan@example.com",
        phone="+1-202-555-0130",
        external_reference="PROVIDER-100",
        status=status,
        version=version,
        created_at=_RECORDED_AT,
        updated_at=_RECORDED_AT,
    )


def _command(
    current: ProfessionalRecord,
    *,
    expected_version: int | None = None,
) -> ArchiveProfessionalCommand:
    return ArchiveProfessionalCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        expected_version=(current.version if expected_version is None else expected_version),
    )


def test_archive_professional_returns_archived_versioned_record() -> None:
    membership_id = uuid4()
    current = _professional_record(membership_id=membership_id)
    archived = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id,
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        archived=archived,
    )
    service = ArchiveProfessionalService(_repository(recording))

    result = service.execute(_command(current))

    assert result.professional is archived
    assert result.professional.status is ProfessionalStatus.ARCHIVED
    assert result.professional.version == 2
    assert result.professional.membership_id == membership_id
    assert recording.get_calls == [(current.tenant_id, current.id)]
    assert recording.archive_calls == [(current.tenant_id, current.id, 1)]


def test_archive_professional_raises_not_found_for_invisible_record() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    service = ArchiveProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            ArchiveProfessionalCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                expected_version=1,
            )
        )

    assert recording.get_calls == [(tenant_id, professional_id)]
    assert recording.archive_calls == []


def test_archive_professional_rejects_stale_version_before_mutation() -> None:
    current = _professional_record(version=4)
    recording = RecordingProfessionalRepository(reads=[current])
    service = ArchiveProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, expected_version=3))

    assert recording.archive_calls == []


def test_archive_professional_rejects_already_archived_record() -> None:
    current = _professional_record(
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(reads=[current])
    service = ArchiveProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalAlreadyArchivedError):
        service.execute(_command(current))

    assert recording.archive_calls == []


def test_archive_professional_classifies_concurrent_archive_as_version_conflict() -> None:
    current = _professional_record(version=1)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        archived=None,
    )
    service = ArchiveProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current))

    assert recording.get_calls == [
        (current.tenant_id, current.id),
        (current.tenant_id, current.id),
    ]
    assert recording.archive_calls == [(current.tenant_id, current.id, 1)]


def test_archive_professional_classifies_concurrent_removal_as_not_found() -> None:
    current = _professional_record(version=1)
    recording = RecordingProfessionalRepository(
        reads=[current, None],
        archived=None,
    )
    service = ArchiveProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(_command(current))

    assert len(recording.archive_calls) == 1


def test_archive_professional_propagates_repository_failure() -> None:
    current = _professional_record()
    error = RuntimeError("database unavailable")
    recording = RecordingProfessionalRepository(
        reads=[current],
        archive_error=error,
    )
    service = ArchiveProfessionalService(_repository(recording))

    with pytest.raises(RuntimeError) as captured:
        service.execute(_command(current))

    assert captured.value is error
    assert len(recording.archive_calls) == 1
