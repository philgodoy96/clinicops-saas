from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
)


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

    def get_by_id_for_update(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> Tenant | None:
        """Return and lock a tenant for a state-sensitive workflow."""

        statement = select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        return session.scalar(statement)

    def get_active_owner_for_update(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> Membership | None:
        """Return and lock the tenant's active owner membership."""

        statement = (
            select(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.role == TenantRole.OWNER,
                Membership.status == MembershipStatus.ACTIVE,
            )
            .with_for_update()
        )
        return session.scalar(statement)

    def get_membership_for_update(
        self,
        session: Session,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Membership | None:
        """Return and lock one user's membership within a tenant."""

        statement = (
            select(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
            )
            .with_for_update()
        )
        return session.scalar(statement)

    def flush(self, session: Session) -> None:
        """Flush tenancy state without committing the transaction."""

        session.flush()
