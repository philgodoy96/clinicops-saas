from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from clinicops.api.dependencies import (
    DatabaseSessionDependency,
)
from clinicops.api.v1.authentication.dependencies import (
    AuthenticatedPrincipalDependency,
)
from clinicops.audit.repositories.audit_log_repository import (
    AuditLogRepository,
)
from clinicops.audit.schemas import AuditLogPageResponse
from clinicops.audit.services.list_audit_logs import (
    ListAuditLogsCommand,
    ListAuditLogsService,
)
from clinicops.authorization.permissions import (
    TenantPermission,
)
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
    RequireTenantPermissionCommand,
    RequireTenantPermissionService,
)
from clinicops.authorization.services.resolve_tenant_context import (
    ResolveTenantContextCommand,
    ResolveTenantContextService,
)

router = APIRouter(
    prefix="/tenants",
    tags=["audit-logs"],
)


def get_audit_log_tenant_context(
    tenant_id: UUID,
    session: DatabaseSessionDependency,
    principal: AuthenticatedPrincipalDependency,
) -> AuthorizedTenantContext:
    tenant_context = ResolveTenantContextService().execute(
        session,
        ResolveTenantContextCommand(
            principal=principal,
            tenant_id=tenant_id,
        ),
    )

    return RequireTenantPermissionService().execute(
        RequireTenantPermissionCommand(
            tenant_context=tenant_context,
            permission=TenantPermission.AUDIT_LOG_READ,
        )
    )


AuditLogTenantContextDependency = Annotated[
    AuthorizedTenantContext,
    Depends(get_audit_log_tenant_context),
]


def get_list_audit_logs_service(
    session: DatabaseSessionDependency,
) -> ListAuditLogsService:
    return ListAuditLogsService(AuditLogRepository(session))


ListAuditLogsServiceDependency = Annotated[
    ListAuditLogsService,
    Depends(get_list_audit_logs_service),
]


@router.get(
    "/{tenant_id}/audit-logs",
    response_model=AuditLogPageResponse,
    summary="List tenant audit logs",
)
def list_tenant_audit_logs(
    authorized_context: AuditLogTenantContextDependency,
    service: ListAuditLogsServiceDependency,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description=("Maximum number of audit entries to return."),
        ),
    ] = 50,
    cursor: Annotated[
        str | None,
        Query(
            max_length=1_024,
            description=("Opaque cursor returned by the previous page."),
        ),
    ] = None,
    action: Annotated[
        str | None,
        Query(
            max_length=100,
            description=("Optional stable audit action filter."),
        ),
    ] = None,
    resource_type: Annotated[
        str | None,
        Query(
            max_length=100,
            description=("Optional audited resource-type filter."),
        ),
    ] = None,
    resource_id: Annotated[
        str | None,
        Query(
            max_length=255,
            description=("Optional audited resource identifier. Requires resource_type."),
        ),
    ] = None,
) -> AuditLogPageResponse:
    return service.execute(
        ListAuditLogsCommand(
            tenant_id=authorized_context.tenant_id,
            membership_role=(authorized_context.role.value),
            limit=limit,
            cursor=cursor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )


__all__ = [
    "AuditLogTenantContextDependency",
    "ListAuditLogsServiceDependency",
    "get_audit_log_tenant_context",
    "get_list_audit_logs_service",
    "router",
]
