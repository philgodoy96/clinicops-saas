from typing import NoReturn

from clinicops.professionals.contracts import (
    ArchivedProfessional,
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


class ArchiveProfessionalService:
    """Archive an active professional using optimistic concurrency."""

    def __init__(
        self,
        repository: ProfessionalRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: ArchiveProfessionalCommand,
    ) -> ArchivedProfessional:
        """Archive a tenant-owned professional at the observed version."""

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

        archived = self._repository.archive_for_tenant(
            tenant_id=command.tenant_id,
            professional_id=command.professional_id,
            expected_version=command.expected_version,
        )
        if archived is None:
            _raise_failed_archive(
                repository=self._repository,
                command=command,
            )

        return ArchivedProfessional(professional=archived)


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


def _raise_failed_archive(
    *,
    repository: ProfessionalRepository,
    command: ArchiveProfessionalCommand,
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

    raise ProfessionalVersionConflictError


__all__ = ["ArchiveProfessionalService"]
