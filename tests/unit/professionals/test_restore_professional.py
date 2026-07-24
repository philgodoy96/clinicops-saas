from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.professionals.contracts import (
    ProfessionalRecord,
    RestoreProfessionalCommand,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalNotArchivedError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.restore_professional import (
    RestoreProfessionalService,
)

_RECORDED_AT = datetime(2026, 7, 24, 21, 30, tzinfo=UTC)


class RecordingProfessionalRepository:
    """Record optimistic professional restore interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        restored: ProfessionalRecord | None = None,
        restore_error: Exception | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._restored = restored
        self._restore_error = restore_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.restore_calls: list[tuple[UUID, UUID, int]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def restore_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        self.restore_calls.append((tenant_id, professional_id, expected_version))
        if self._restore_error is not None:
            raise self._restore_error
        return self._restored


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _professional_record(
    *,
    tenant_id: UUID | None = None,
    professional_id: UUID | None = None,
    membership_id: UUID | None = None,
    status: ProfessionalStatus = ProfessionalStatus.ARCHIVED,
    version: int = 2,
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
) -> RestoreProfessionalCommand:
    return RestoreProfessionalCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        expected_version=(current.version if expected_version is None else expected_version),
    )


def test_restore_professional_returns_active_versioned_record() -> None:
    membership_id = uuid4()
    current = _professional_record(membership_id=membership_id)
    restored = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id,
        status=ProfessionalStatus.ACTIVE,
        version=3,
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        restored=restored,
    )
    service = RestoreProfessionalService(_repository(recording))

    result = service.execute(_command(current))

    assert result.professional is restored
    assert result.professional.status is ProfessionalStatus.ACTIVE
    assert result.professional.version == 3
    assert result.professional.membership_id == membership_id
    assert recording.get_calls == [(current.tenant_id, current.id)]
    assert recording.restore_calls == [(current.tenant_id, current.id, 2)]


def test_restore_professional_raises_not_found_for_invisible_record() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    service = RestoreProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            RestoreProfessionalCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                expected_version=1,
            )
        )

    assert recording.get_calls == [(tenant_id, professional_id)]
    assert recording.restore_calls == []


def test_restore_professional_rejects_stale_version_before_mutation() -> None:
    current = _professional_record(version=4)
    recording = RecordingProfessionalRepository(reads=[current])
    service = RestoreProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, expected_version=3))

    assert recording.restore_calls == []


def test_restore_professional_rejects_active_record() -> None:
    current = _professional_record(
        status=ProfessionalStatus.ACTIVE,
        version=3,
    )
    recording = RecordingProfessionalRepository(reads=[current])
    service = RestoreProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalNotArchivedError):
        service.execute(_command(current))

    assert recording.restore_calls == []


def test_restore_professional_classifies_concurrent_restore_as_version_conflict() -> None:
    current = _professional_record(version=2)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        status=ProfessionalStatus.ACTIVE,
        version=3,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        restored=None,
    )
    service = RestoreProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current))

    assert recording.get_calls == [
        (current.tenant_id, current.id),
        (current.tenant_id, current.id),
    ]
    assert recording.restore_calls == [(current.tenant_id, current.id, 2)]


def test_restore_professional_classifies_concurrent_removal_as_not_found() -> None:
    current = _professional_record(version=2)
    recording = RecordingProfessionalRepository(
        reads=[current, None],
        restored=None,
    )
    service = RestoreProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(_command(current))

    assert len(recording.restore_calls) == 1


def test_restore_professional_propagates_repository_failure() -> None:
    current = _professional_record()
    error = RuntimeError("database unavailable")
    recording = RecordingProfessionalRepository(
        reads=[current],
        restore_error=error,
    )
    service = RestoreProfessionalService(_repository(recording))

    with pytest.raises(RuntimeError) as captured:
        service.execute(_command(current))

    assert captured.value is error
    assert len(recording.restore_calls) == 1
