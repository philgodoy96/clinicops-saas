from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.professionals.contracts import (
    ProfessionalRecord,
    UnlinkProfessionalMembershipCommand,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalAlreadyArchivedError,
    ProfessionalNotFoundError,
    ProfessionalNotLinkedError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.unlink_professional_membership import (
    UnlinkProfessionalMembershipService,
)

_RECORDED_AT = datetime(2026, 7, 24, 22, 30, tzinfo=UTC)


class RecordingProfessionalRepository:
    """Record explicit professional-membership unlink interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        unlinked: ProfessionalRecord | None = None,
        unlink_error: Exception | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._unlinked = unlinked
        self._unlink_error = unlink_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.unlink_calls: list[tuple[UUID, UUID, int]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def unlink_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        self.unlink_calls.append((tenant_id, professional_id, expected_version))
        if self._unlink_error is not None:
            raise self._unlink_error
        return self._unlinked


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
) -> UnlinkProfessionalMembershipCommand:
    return UnlinkProfessionalMembershipCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        expected_version=(current.version if expected_version is None else expected_version),
    )


def test_unlink_professional_membership_returns_previous_membership() -> None:
    membership_id = uuid4()
    current = _professional_record(membership_id=membership_id)
    unlinked = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=None,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        unlinked=unlinked,
    )
    service = UnlinkProfessionalMembershipService(_repository(recording))

    result = service.execute(_command(current))

    assert result.professional is unlinked
    assert result.professional.membership_id is None
    assert result.professional.version == 2
    assert result.previous_membership_id == membership_id
    assert recording.unlink_calls == [(current.tenant_id, current.id, 1)]


def test_unlink_professional_membership_raises_not_found() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    service = UnlinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            UnlinkProfessionalMembershipCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                expected_version=1,
            )
        )

    assert recording.unlink_calls == []


def test_unlink_professional_membership_rejects_stale_version() -> None:
    current = _professional_record(
        membership_id=uuid4(),
        version=4,
    )
    recording = RecordingProfessionalRepository(reads=[current])
    service = UnlinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, expected_version=3))

    assert recording.unlink_calls == []


def test_unlink_professional_membership_rejects_archived_professional() -> None:
    current = _professional_record(
        membership_id=uuid4(),
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(reads=[current])
    service = UnlinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalAlreadyArchivedError):
        service.execute(_command(current))

    assert recording.unlink_calls == []


def test_unlink_professional_membership_rejects_unlinked_professional() -> None:
    current = _professional_record(membership_id=None)
    recording = RecordingProfessionalRepository(reads=[current])
    service = UnlinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalNotLinkedError):
        service.execute(_command(current))

    assert recording.unlink_calls == []


def test_unlink_professional_membership_classifies_concurrent_winner() -> None:
    membership_id = uuid4()
    current = _professional_record(
        membership_id=membership_id,
        version=1,
    )
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=None,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        unlinked=None,
    )
    service = UnlinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current))

    assert recording.get_calls == [
        (current.tenant_id, current.id),
        (current.tenant_id, current.id),
    ]
    assert len(recording.unlink_calls) == 1


def test_unlink_professional_membership_propagates_repository_failure() -> None:
    current = _professional_record(membership_id=uuid4())
    error = RuntimeError("database unavailable")
    recording = RecordingProfessionalRepository(
        reads=[current],
        unlink_error=error,
    )
    service = UnlinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(RuntimeError) as captured:
        service.execute(_command(current))

    assert captured.value is error
    assert len(recording.unlink_calls) == 1
