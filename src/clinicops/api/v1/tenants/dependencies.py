from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from clinicops.api.dependencies import DatabaseSessionDependency
from clinicops.api.v1.authentication.dependencies import (
    AuthenticatedPrincipalDependency,
)
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
    RequireTenantPermissionCommand,
    RequireTenantPermissionService,
)
from clinicops.authorization.services.resolve_tenant_context import (
    ResolveTenantContextCommand,
    ResolveTenantContextService,
    TenantContext,
)


def get_resolve_tenant_context_service() -> ResolveTenantContextService:
    """Build the persisted tenant-context resolution service."""

    return ResolveTenantContextService()


ResolveTenantContextServiceDependency = Annotated[
    ResolveTenantContextService,
    Depends(get_resolve_tenant_context_service),
]


def get_tenant_context(
    tenant_id: UUID,
    principal: AuthenticatedPrincipalDependency,
    session: DatabaseSessionDependency,
    service: ResolveTenantContextServiceDependency,
) -> TenantContext:
    """Resolve trusted tenant context from current persisted state."""

    return service.execute(
        session,
        ResolveTenantContextCommand(
            principal=principal,
            tenant_id=tenant_id,
        ),
    )


TenantContextDependency = Annotated[
    TenantContext,
    Depends(get_tenant_context),
]


def get_require_tenant_permission_service() -> RequireTenantPermissionService:
    """Build the tenant permission enforcement service."""

    return RequireTenantPermissionService()


RequireTenantPermissionServiceDependency = Annotated[
    RequireTenantPermissionService,
    Depends(get_require_tenant_permission_service),
]


def require_tenant_permission(
    permission: TenantPermission,
) -> Callable[..., AuthorizedTenantContext]:
    """Build a dependency enforcing one tenant permission."""

    def dependency(
        tenant_context: TenantContextDependency,
        service: RequireTenantPermissionServiceDependency,
    ) -> AuthorizedTenantContext:
        return service.execute(
            RequireTenantPermissionCommand(
                tenant_context=tenant_context,
                permission=permission,
            )
        )

    return dependency
