from clinicops.professionals.contracts import (
    GetProfessionalCommand,
    ProfessionalRecord,
)
from clinicops.professionals.exceptions import ProfessionalNotFoundError
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)


class GetProfessionalService:
    """Retrieve one professional through an explicit tenant boundary."""

    def __init__(
        self,
        repository: ProfessionalRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: GetProfessionalCommand,
    ) -> ProfessionalRecord:
        """Return a tenant-owned professional or raise not found."""

        professional = self._repository.get_by_id_for_tenant(
            tenant_id=command.tenant_id,
            professional_id=command.professional_id,
        )
        if professional is None:
            raise ProfessionalNotFoundError

        return professional


__all__ = ["GetProfessionalService"]
