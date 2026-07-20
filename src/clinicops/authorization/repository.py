from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from clinicops.tenancy.models import Membership, Tenant


@dataclass(frozen=True, slots=True)
class TenantContextState:
    """Current tenant and membership state for one global user."""

    tenant: Tenant
    membership: Membership | None


class AuthorizationRepository:
    """Read operations required to resolve tenant authorization."""

    def get_tenant_context_state(
        self,
        session: Session,
        *,
        user_id: UUID,
        tenant_id: UUID,
    ) -> TenantContextState | None:
        """Return tenant and user-scoped membership without row locking."""

        row = session.execute(
            select(Tenant, Membership)
            .outerjoin(
                Membership,
                and_(
                    Membership.tenant_id == Tenant.id,
                    Membership.user_id == user_id,
                ),
            )
            .where(Tenant.id == tenant_id)
        ).one_or_none()

        if row is None:
            return None

        tenant, membership = row

        return TenantContextState(
            tenant=tenant,
            membership=membership,
        )
