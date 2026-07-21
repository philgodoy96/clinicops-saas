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
from clinicops.invitations.services.issue_invitation import (
    IssueInvitationService,
)
from clinicops.invitations.services.queries import (
    ListTenantInvitationsService,
)
from clinicops.invitations.services.revoke_invitation import (
    RevokeInvitationService,
)
from clinicops.tenancy.services.change_membership_role import (
    ChangeMembershipRoleService,
)
from clinicops.tenancy.services.create_tenant import (
    CreateTenantService,
)
from clinicops.tenancy.services.disable_membership import (
    DisableMembershipService,
)
from clinicops.tenancy.services.enable_membership import (
    EnableMembershipService,
)
from clinicops.tenancy.services.queries import (
    GetTenantDetailsService,
    ListAvailableTenantsService,
    ListTenantMembershipsService,
)
from clinicops.tenancy.services.remove_membership import (
    RemoveMembershipService,
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


def get_create_tenant_service() -> CreateTenantService:
    """Build the tenant creation service."""

    return CreateTenantService()


CreateTenantServiceDependency = Annotated[
    CreateTenantService,
    Depends(get_create_tenant_service),
]


def get_change_membership_role_service() -> ChangeMembershipRoleService:
    """Build the membership role-management service."""

    return ChangeMembershipRoleService()


ChangeMembershipRoleServiceDependency = Annotated[
    ChangeMembershipRoleService,
    Depends(get_change_membership_role_service),
]


def get_disable_membership_service() -> DisableMembershipService:
    """Build the membership-disable service."""

    return DisableMembershipService()


DisableMembershipServiceDependency = Annotated[
    DisableMembershipService,
    Depends(get_disable_membership_service),
]


def get_enable_membership_service() -> EnableMembershipService:
    """Build the membership-enable service."""

    return EnableMembershipService()


EnableMembershipServiceDependency = Annotated[
    EnableMembershipService,
    Depends(get_enable_membership_service),
]


def get_remove_membership_service() -> RemoveMembershipService:
    """Build the membership-removal service."""

    return RemoveMembershipService()


RemoveMembershipServiceDependency = Annotated[
    RemoveMembershipService,
    Depends(get_remove_membership_service),
]


def get_list_available_tenants_service() -> ListAvailableTenantsService:
    """Build the available-tenant query service."""

    return ListAvailableTenantsService()


ListAvailableTenantsServiceDependency = Annotated[
    ListAvailableTenantsService,
    Depends(get_list_available_tenants_service),
]


def get_tenant_details_service() -> GetTenantDetailsService:
    """Build the tenant-detail query service."""

    return GetTenantDetailsService()


GetTenantDetailsServiceDependency = Annotated[
    GetTenantDetailsService,
    Depends(get_tenant_details_service),
]


def get_list_tenant_memberships_service() -> ListTenantMembershipsService:
    """Build the tenant-membership query service."""

    return ListTenantMembershipsService()


ListTenantMembershipsServiceDependency = Annotated[
    ListTenantMembershipsService,
    Depends(get_list_tenant_memberships_service),
]


def get_list_tenant_invitations_service() -> ListTenantInvitationsService:
    """Build the tenant-invitation query service."""

    return ListTenantInvitationsService()


ListTenantInvitationsServiceDependency = Annotated[
    ListTenantInvitationsService,
    Depends(get_list_tenant_invitations_service),
]


def get_issue_invitation_service() -> IssueInvitationService:
    """Build the invitation issuance service."""

    return IssueInvitationService()


IssueInvitationServiceDependency = Annotated[
    IssueInvitationService,
    Depends(get_issue_invitation_service),
]


def get_revoke_invitation_service() -> RevokeInvitationService:
    """Build the invitation revocation service."""

    return RevokeInvitationService()


RevokeInvitationServiceDependency = Annotated[
    RevokeInvitationService,
    Depends(get_revoke_invitation_service),
]
