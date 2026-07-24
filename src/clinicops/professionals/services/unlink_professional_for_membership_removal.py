from collections.abc import Callable

from sqlalchemy.orm import Session

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
    ) -> None:
        self._repository_factory = repository_factory

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

        return UnlinkedProfessionalForMembershipRemoval(
            professional=professional,
            previous_membership_id=(command.membership_id if professional is not None else None),
        )


__all__ = [
    "ProfessionalRepositoryFactory",
    "UnlinkProfessionalForMembershipRemovalService",
]
