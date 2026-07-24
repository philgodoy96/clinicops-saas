from typing import NoReturn

from clinicops.professionals.contracts import (
    ProfessionalRecord,
    RestoredProfessional,
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


class RestoreProfessionalService:
    """Restore an archived professional using optimistic concurrency."""

    def __init__(
        self,
        repository: ProfessionalRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: RestoreProfessionalCommand,
    ) -> RestoredProfessional:
        """Restore a tenant-owned professional at the observed version."""

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
        _require_archived_professional(current)

        restored = self._repository.restore_for_tenant(
            tenant_id=command.tenant_id,
            professional_id=command.professional_id,
            expected_version=command.expected_version,
        )
        if restored is None:
            _raise_failed_restore(
                repository=self._repository,
                command=command,
            )

        return RestoredProfessional(professional=restored)


def _require_current_version(
    professional: ProfessionalRecord,
    *,
    expected_version: int,
) -> None:
    if professional.version != expected_version:
        raise ProfessionalVersionConflictError


def _require_archived_professional(
    professional: ProfessionalRecord,
) -> None:
    if professional.status is ProfessionalStatus.ACTIVE:
        raise ProfessionalNotArchivedError


def _raise_failed_restore(
    *,
    repository: ProfessionalRepository,
    command: RestoreProfessionalCommand,
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
    _require_archived_professional(latest)

    raise ProfessionalVersionConflictError


__all__ = ["RestoreProfessionalService"]
