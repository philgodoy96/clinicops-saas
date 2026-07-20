from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
)
from clinicops.authorization.exceptions import (
    TenantDisabledError,
    TenantMembershipDisabledError,
    TenantMembershipNotFoundError,
    TenantNotFoundError,
)
from clinicops.authorization.repository import AuthorizationRepository
from clinicops.tenancy.models import (
    MembershipStatus,
    TenantRole,
    TenantStatus,
)


@dataclass(frozen=True, slots=True)
class ResolveTenantContextCommand:
    """Trusted principal and explicitly selected tenant."""

    principal: AuthenticatedPrincipal
    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Trusted tenant-scoped identity derived from current state."""

    user_id: UUID
    session_id: UUID
    tenant_id: UUID
    membership_id: UUID
    role: TenantRole


class ResolveTenantContextService:
    """Resolve tenant context without locking database rows."""

    def __init__(
        self,
        authorization_repository: AuthorizationRepository | None = None,
    ) -> None:
        self._authorization_repository = (
            authorization_repository
            if authorization_repository is not None
            else AuthorizationRepository()
        )

    def execute(
        self,
        session: Session,
        command: ResolveTenantContextCommand,
    ) -> TenantContext:
        """Validate selected tenant and current membership state."""

        context_state = self._authorization_repository.get_tenant_context_state(
            session,
            user_id=command.principal.user_id,
            tenant_id=command.tenant_id,
        )

        if context_state is None:
            raise TenantNotFoundError()

        tenant = context_state.tenant
        membership = context_state.membership

        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantDisabledError()

        if membership is None:
            raise TenantMembershipNotFoundError()

        if membership.status is not MembershipStatus.ACTIVE:
            raise TenantMembershipDisabledError()

        return TenantContext(
            user_id=command.principal.user_id,
            session_id=command.principal.session_id,
            tenant_id=tenant.id,
            membership_id=membership.id,
            role=membership.role,
        )
