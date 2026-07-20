from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantStatus,
)


class TenantQueryRepository:
    """Read-only tenant and membership persistence queries."""

    def list_available_memberships(
        self,
        session: Session,
        user_id: UUID,
    ) -> list[Membership]:
        """Return active memberships for active tenants."""

        statement = (
            select(Membership)
            .join(Membership.tenant)
            .options(joinedload(Membership.tenant))
            .where(
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.ACTIVE,
                Tenant.status == TenantStatus.ACTIVE,
            )
            .order_by(
                Tenant.name.asc(),
                Tenant.id.asc(),
            )
        )

        return list(session.scalars(statement).unique().all())

    def get_tenant(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> Tenant | None:
        """Return one tenant without acquiring a row lock."""

        statement = select(Tenant).where(Tenant.id == tenant_id)

        return session.scalar(statement)

    def list_memberships(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> list[Membership]:
        """Return all memberships owned by one tenant."""

        statement = (
            select(Membership)
            .where(Membership.tenant_id == tenant_id)
            .order_by(
                Membership.created_at.asc(),
                Membership.id.asc(),
            )
        )

        return list(session.scalars(statement).all())
