from dataclasses import dataclass
from uuid import UUID

from clinicops.authorization.exceptions import (
    TenantPermissionDeniedError,
)
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.policy import role_has_permission
from clinicops.authorization.services.resolve_tenant_context import (
    TenantContext,
)
from clinicops.tenancy.models import TenantRole


@dataclass(frozen=True, slots=True)
class RequireTenantPermissionCommand:
    """Trusted tenant context and required application capability."""

    tenant_context: TenantContext
    permission: TenantPermission


@dataclass(frozen=True, slots=True)
class AuthorizedTenantContext:
    """Tenant context proven to grant one explicit permission."""

    user_id: UUID
    session_id: UUID
    tenant_id: UUID
    membership_id: UUID
    role: TenantRole
    granted_permission: TenantPermission


class RequireTenantPermissionService:
    """Evaluate one permission against the trusted tenant role."""

    def execute(
        self,
        command: RequireTenantPermissionCommand,
    ) -> AuthorizedTenantContext:
        """Return authorized context or raise a generic denial."""

        tenant_context = command.tenant_context

        if not role_has_permission(
            tenant_context.role,
            command.permission,
        ):
            raise TenantPermissionDeniedError()

        return AuthorizedTenantContext(
            user_id=tenant_context.user_id,
            session_id=tenant_context.session_id,
            tenant_id=tenant_context.tenant_id,
            membership_id=tenant_context.membership_id,
            role=tenant_context.role,
            granted_permission=command.permission,
        )
