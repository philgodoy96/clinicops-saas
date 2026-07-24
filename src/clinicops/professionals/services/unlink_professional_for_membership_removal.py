from collections.abc import Callable

from sqlalchemy.orm import Session

from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.professionals.audit import (
    professional_membership_unlinked_audit_command,
)
from clinicops.professionals.contracts import (
    UnlinkedProfessionalForMembershipRemoval,
    UnlinkProfessionalForMembershipRemovalCommand,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)

ProfessionalRepositoryFactory = Callable[
    [Session],
    ProfessionalRepository,
]


class UnlinkProfessionalForMembershipRemovalService:
    """Clear a professional link before its Membership is removed."""

    def __init__(
        self,
        repository_factory: ProfessionalRepositoryFactory = ProfessionalRepository,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._repository_factory = repository_factory
        self._audit_recorder = (
            audit_recorder if audit_recorder is not None else SqlAlchemyAuditRecorder()
        )

    def execute(
        self,
        session: Session,
        command: UnlinkProfessionalForMembershipRemovalCommand,
    ) -> UnlinkedProfessionalForMembershipRemoval:
        """Unlink through the caller-owned transaction without committing."""

        repository = self._repository_factory(session)
        professional = repository.unlink_by_membership_for_tenant(
            tenant_id=command.tenant_id,
            membership_id=command.membership_id,
        )

        if professional is None:
            return UnlinkedProfessionalForMembershipRemoval(
                professional=None,
                previous_membership_id=None,
            )

        self._audit_recorder.record(
            session,
            professional_membership_unlinked_audit_command(
                professional=professional,
                membership_id=command.membership_id,
                reason="membership_removal",
                audit_context=command.audit_context,
            ),
        )

        return UnlinkedProfessionalForMembershipRemoval(
            professional=professional,
            previous_membership_id=command.membership_id,
        )


__all__ = [
    "ProfessionalRepositoryFactory",
    "UnlinkProfessionalForMembershipRemovalService",
]
