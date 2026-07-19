from sqlalchemy.orm import Session

from clinicops.tenancy.models import Tenant


class TenantRepository:
    """Persistence operations for tenants and their memberships."""

    def add_and_flush(
        self,
        session: Session,
        tenant: Tenant,
    ) -> None:
        """Add a tenant aggregate and flush the current unit of work."""

        session.add(tenant)
        session.flush()
