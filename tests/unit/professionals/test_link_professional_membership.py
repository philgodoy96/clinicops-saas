from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.professionals.contracts import (
    LinkProfessionalMembershipCommand,
    ProfessionalRecord,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalAlreadyArchivedError,
    ProfessionalAlreadyLinkedError,
    ProfessionalMembershipInactiveError,
    ProfessionalMembershipLinkConflictError,
    ProfessionalMembershipNotFoundError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.link_professional_membership import (
    LinkProfessionalMembershipService,
)
from clinicops.tenancy.models import MembershipStatus

_RECORDED_AT = datetime(2026, 7, 24, 22, 0, tzinfo=UTC)


class RecordingProfessionalRepository:
    """Record explicit professional-membership link interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        membership_status: MembershipStatus | None = MembershipStatus.ACTIVE,
        linked: ProfessionalRecord | None = None,
        link_error: Exception | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._membership_status = membership_status
        self._linked = linked
        self._link_error = link_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.membership_calls: list[tuple[UUID, UUID]] = []
        self.link_calls: list[tuple[UUID, UUID, UUID, int]] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def get_membership_status_for_tenant_for_update(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> MembershipStatus | None:
        self.membership_calls.append((tenant_id, membership_id))
        return self._membership_status

    def link_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        membership_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        self.link_calls.append(
            (
                tenant_id,
                professional_id,
                membership_id,
                expected_version,
            )
        )
        if self._link_error is not None:
            raise self._link_error
        return self._linked


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
    membership_id: UUID | None = None,
    expected_version: int | None = None,
) -> LinkProfessionalMembershipCommand:
    return LinkProfessionalMembershipCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id or uuid4(),
        expected_version=(current.version if expected_version is None else expected_version),
    )


def test_link_professional_membership_returns_versioned_link() -> None:
    membership_id = uuid4()
    current = _professional_record()
    linked = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        linked=linked,
    )
    service = LinkProfessionalMembershipService(_repository(recording))
    command = _command(current, membership_id=membership_id)

    result = service.execute(command)

    assert result.professional is linked
    assert result.professional.membership_id == membership_id
    assert result.professional.version == 2
    assert recording.get_calls == [(current.tenant_id, current.id)]
    assert recording.membership_calls == [(current.tenant_id, membership_id)]
    assert recording.link_calls == [
        (
            current.tenant_id,
            current.id,
            membership_id,
            1,
        )
    ]


def test_link_professional_membership_raises_not_found_for_professional() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    service = LinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            LinkProfessionalMembershipCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                membership_id=uuid4(),
                expected_version=1,
            )
        )

    assert recording.membership_calls == []
    assert recording.link_calls == []


def test_link_professional_membership_rejects_stale_version() -> None:
    current = _professional_record(version=4)
    recording = RecordingProfessionalRepository(reads=[current])
    service = LinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, expected_version=3))

    assert recording.membership_calls == []
    assert recording.link_calls == []


def test_link_professional_membership_rejects_archived_professional() -> None:
    current = _professional_record(
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(reads=[current])
    service = LinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalAlreadyArchivedError):
        service.execute(_command(current))

    assert recording.membership_calls == []
    assert recording.link_calls == []


def test_link_professional_membership_rejects_already_linked_professional() -> None:
    current = _professional_record(membership_id=uuid4())
    recording = RecordingProfessionalRepository(reads=[current])
    service = LinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalAlreadyLinkedError):
        service.execute(_command(current))

    assert recording.membership_calls == []
    assert recording.link_calls == []


def test_link_professional_membership_hides_foreign_or_missing_membership() -> None:
    current = _professional_record()
    membership_id = uuid4()
    recording = RecordingProfessionalRepository(
        reads=[current],
        membership_status=None,
    )
    service = LinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalMembershipNotFoundError):
        service.execute(_command(current, membership_id=membership_id))

    assert recording.membership_calls == [(current.tenant_id, membership_id)]
    assert recording.link_calls == []


def test_link_professional_membership_rejects_inactive_membership() -> None:
    current = _professional_record()
    membership_id = uuid4()
    recording = RecordingProfessionalRepository(
        reads=[current],
        membership_status=MembershipStatus.DISABLED,
    )
    service = LinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalMembershipInactiveError):
        service.execute(_command(current, membership_id=membership_id))

    assert recording.link_calls == []


def test_link_professional_membership_propagates_membership_conflict() -> None:
    current = _professional_record()
    error = ProfessionalMembershipLinkConflictError()
    recording = RecordingProfessionalRepository(
        reads=[current],
        link_error=error,
    )
    service = LinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalMembershipLinkConflictError) as captured:
        service.execute(_command(current))

    assert captured.value is error
    assert len(recording.link_calls) == 1


def test_link_professional_membership_classifies_concurrent_winner() -> None:
    membership_id = uuid4()
    current = _professional_record(version=1)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        membership_id=membership_id,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        linked=None,
    )
    service = LinkProfessionalMembershipService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(_command(current, membership_id=membership_id))

    assert recording.get_calls == [
        (current.tenant_id, current.id),
        (current.tenant_id, current.id),
    ]
    assert len(recording.link_calls) == 1
