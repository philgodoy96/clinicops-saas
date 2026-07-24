from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from clinicops.professionals.contracts import (
    ProfessionalRecord,
    UnlinkProfessionalForMembershipRemovalCommand,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.unlink_professional_for_membership_removal import (
    UnlinkProfessionalForMembershipRemovalService,
)

_RECORDED_AT = datetime(2026, 7, 24, 23, 0, tzinfo=UTC)


class RecordingProfessionalRepository:
    """Record Membership-removal unlink interactions."""

    def __init__(
        self,
        *,
        unlinked: ProfessionalRecord | None,
    ) -> None:
        self._unlinked = unlinked
        self.calls: list[tuple[UUID, UUID]] = []

    def unlink_by_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> ProfessionalRecord | None:
        self.calls.append((tenant_id, membership_id))
        return self._unlinked


def _professional_record(
    *,
    tenant_id: UUID,
    membership_id: UUID | None,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    version: int = 2,
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=uuid4(),
        tenant_id=tenant_id,
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


def test_membership_removal_unlinks_professional_in_caller_session() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    unlinked = _professional_record(
        tenant_id=tenant_id,
        membership_id=None,
    )
    recording = RecordingProfessionalRepository(unlinked=unlinked)
    received_sessions: list[Session] = []

    def repository_factory(session: Session) -> ProfessionalRepository:
        received_sessions.append(session)
        return cast(ProfessionalRepository, recording)

    service = UnlinkProfessionalForMembershipRemovalService(repository_factory)
    session = cast(Session, object())

    result = service.execute(
        session,
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=tenant_id,
            membership_id=membership_id,
        ),
    )

    assert result.professional is unlinked
    assert result.previous_membership_id == membership_id
    assert received_sessions == [session]
    assert recording.calls == [(tenant_id, membership_id)]


def test_membership_removal_preserves_archived_professional_unlink_result() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    unlinked = _professional_record(
        tenant_id=tenant_id,
        membership_id=None,
        status=ProfessionalStatus.ARCHIVED,
        version=5,
    )
    recording = RecordingProfessionalRepository(unlinked=unlinked)
    service = UnlinkProfessionalForMembershipRemovalService(
        lambda session: cast(ProfessionalRepository, recording)
    )

    result = service.execute(
        cast(Session, object()),
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=tenant_id,
            membership_id=membership_id,
        ),
    )

    assert result.professional is unlinked
    assert result.professional.status is ProfessionalStatus.ARCHIVED
    assert result.professional.version == 5
    assert result.previous_membership_id == membership_id


def test_membership_removal_is_no_op_when_no_professional_is_linked() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    recording = RecordingProfessionalRepository(unlinked=None)
    service = UnlinkProfessionalForMembershipRemovalService(
        lambda session: cast(ProfessionalRepository, recording)
    )

    result = service.execute(
        cast(Session, object()),
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=tenant_id,
            membership_id=membership_id,
        ),
    )

    assert result.professional is None
    assert result.previous_membership_id is None
    assert recording.calls == [(tenant_id, membership_id)]


def test_membership_removal_propagates_repository_failure() -> None:
    class FailingProfessionalRepository:
        def unlink_by_membership_for_tenant(
            self,
            *,
            tenant_id: UUID,
            membership_id: UUID,
        ) -> ProfessionalRecord | None:
            del tenant_id, membership_id
            raise RuntimeError("database unavailable")

    service = UnlinkProfessionalForMembershipRemovalService(
        lambda session: cast(
            ProfessionalRepository,
            FailingProfessionalRepository(),
        )
    )

    try:
        service.execute(
            cast(Session, object()),
            UnlinkProfessionalForMembershipRemovalCommand(
                tenant_id=uuid4(),
                membership_id=uuid4(),
            ),
        )
    except RuntimeError as error:
        assert str(error) == "database unavailable"
    else:
        raise AssertionError("Expected repository failure to propagate.")
