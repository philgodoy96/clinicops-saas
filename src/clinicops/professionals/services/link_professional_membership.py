from typing import NoReturn

from clinicops.professionals.contracts import (
    LinkedProfessionalMembership,
    LinkProfessionalMembershipCommand,
    ProfessionalRecord,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.exceptions import (
    ProfessionalAlreadyArchivedError,
    ProfessionalAlreadyLinkedError,
    ProfessionalMembershipInactiveError,
    ProfessionalMembershipNotFoundError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.tenancy.models import MembershipStatus


class LinkProfessionalMembershipService:
    """Link an active professional to an active tenant membership."""

    def __init__(
        self,
        repository: ProfessionalRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: LinkProfessionalMembershipCommand,
    ) -> LinkedProfessionalMembership:
        """Link a membership through tenant and version guards."""

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
        _require_unlinked_professional(current)

        membership_status = self._repository.get_membership_status_for_tenant_for_update(
            tenant_id=command.tenant_id,
            membership_id=command.membership_id,
        )
        if membership_status is None:
            raise ProfessionalMembershipNotFoundError
        if membership_status is not MembershipStatus.ACTIVE:
            raise ProfessionalMembershipInactiveError

        linked = self._repository.link_membership_for_tenant(
            tenant_id=command.tenant_id,
            professional_id=command.professional_id,
            membership_id=command.membership_id,
            expected_version=command.expected_version,
        )
        if linked is None:
            _raise_failed_link(
                repository=self._repository,
                command=command,
            )

        return LinkedProfessionalMembership(professional=linked)


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


def _require_unlinked_professional(
    professional: ProfessionalRecord,
) -> None:
    if professional.membership_id is not None:
        raise ProfessionalAlreadyLinkedError


def _raise_failed_link(
    *,
    repository: ProfessionalRepository,
    command: LinkProfessionalMembershipCommand,
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
    _require_unlinked_professional(latest)

    raise ProfessionalVersionConflictError


__all__ = ["LinkProfessionalMembershipService"]
