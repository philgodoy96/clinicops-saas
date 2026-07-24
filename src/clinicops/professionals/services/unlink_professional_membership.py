from typing import NoReturn
from uuid import UUID

from clinicops.professionals.contracts import (
    ProfessionalRecord,
    UnlinkedProfessionalMembership,
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


class UnlinkProfessionalMembershipService:
    """Unlink an active professional from its current membership."""

    def __init__(
        self,
        repository: ProfessionalRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: UnlinkProfessionalMembershipCommand,
    ) -> UnlinkedProfessionalMembership:
        """Clear an existing membership link at the observed version."""

        current = self._repository.get_by_id_for_tenant(
            tenant_id=command.tenant_id,
            professional_id=command.professional_id,
        )
        if current is None:
            raise ProfessionalNotFoundError

        _require_current_version(
            current,
            expected_version=command.expected_version,
        )
        _require_active_professional(current)
        previous_membership_id = _require_linked_professional(current)

        unlinked = self._repository.unlink_membership_for_tenant(
            tenant_id=command.tenant_id,
            professional_id=command.professional_id,
            expected_version=command.expected_version,
        )
        if unlinked is None:
            _raise_failed_unlink(
                repository=self._repository,
                command=command,
            )

        return UnlinkedProfessionalMembership(
            professional=unlinked,
            previous_membership_id=previous_membership_id,
        )


def _require_current_version(
    professional: ProfessionalRecord,
    *,
    expected_version: int,
) -> None:
    if professional.version != expected_version:
        raise ProfessionalVersionConflictError


def _require_active_professional(
    professional: ProfessionalRecord,
) -> None:
    if professional.status is ProfessionalStatus.ARCHIVED:
        raise ProfessionalAlreadyArchivedError


def _require_linked_professional(
    professional: ProfessionalRecord,
) -> UUID:
    if professional.membership_id is None:
        raise ProfessionalNotLinkedError

    return professional.membership_id


def _raise_failed_unlink(
    *,
    repository: ProfessionalRepository,
    command: UnlinkProfessionalMembershipCommand,
) -> NoReturn:
    latest = repository.get_by_id_for_tenant(
        tenant_id=command.tenant_id,
        professional_id=command.professional_id,
    )
    if latest is None:
        raise ProfessionalNotFoundError

    _require_current_version(
        latest,
        expected_version=command.expected_version,
    )
    _require_active_professional(latest)
    _require_linked_professional(latest)

    raise ProfessionalVersionConflictError


__all__ = ["UnlinkProfessionalMembershipService"]
