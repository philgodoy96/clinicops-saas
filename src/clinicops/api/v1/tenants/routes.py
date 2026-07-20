from typing import Annotated

from fastapi import APIRouter, Depends, status

from clinicops.api.dependencies import DatabaseSessionDependency
from clinicops.api.v1.authentication.dependencies import (
    AuthenticatedPrincipalDependency,
)
from clinicops.api.v1.tenants.dependencies import (
    GetTenantDetailsServiceDependency,
    ListAvailableTenantsServiceDependency,
    ListTenantMembershipsServiceDependency,
    require_tenant_permission,
)
from clinicops.api.v1.tenants.schemas import (
    CurrentMembershipResponse,
    MembershipListResponse,
    MembershipResponse,
    TenantDetailResponse,
    TenantListResponse,
    TenantSummaryResponse,
)
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.tenancy.models import MembershipStatus
from clinicops.tenancy.services.queries import (
    AvailableTenant,
    GetTenantDetailsCommand,
    ListAvailableTenantsCommand,
    ListTenantMembershipsCommand,
    TenantDetails,
    TenantMembership,
)

router = APIRouter(
    prefix="/tenants",
    tags=["tenants"],
)

TenantReadAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(
        require_tenant_permission(
            TenantPermission.TENANT_READ,
        )
    ),
]
MemberReadAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(
        require_tenant_permission(
            TenantPermission.MEMBER_READ,
        )
    ),
]


def _tenant_summary_response(
    result: AvailableTenant,
) -> TenantSummaryResponse:
    """Translate available tenant access into its HTTP contract."""

    return TenantSummaryResponse(
        id=result.tenant_id,
        name=result.tenant_name,
        status=result.tenant_status,
        current_membership=CurrentMembershipResponse(
            id=result.membership_id,
            role=result.membership_role,
            status=result.membership_status,
        ),
    )


def _tenant_detail_response(
    result: TenantDetails,
    context: AuthorizedTenantContext,
) -> TenantDetailResponse:
    """Translate tenant details and trusted current access."""

    return TenantDetailResponse(
        id=result.id,
        name=result.name,
        status=result.status,
        created_at=result.created_at,
        updated_at=result.updated_at,
        disabled_at=result.disabled_at,
        current_membership=CurrentMembershipResponse(
            id=context.membership_id,
            role=context.role,
            status=MembershipStatus.ACTIVE,
        ),
    )


def _membership_response(
    result: TenantMembership,
) -> MembershipResponse:
    """Translate one membership into its public HTTP contract."""

    return MembershipResponse(
        id=result.id,
        tenant_id=result.tenant_id,
        user_id=result.user_id,
        role=result.role,
        status=result.status,
        created_at=result.created_at,
        updated_at=result.updated_at,
        disabled_at=result.disabled_at,
    )


@router.get(
    "",
    response_model=TenantListResponse,
    status_code=status.HTTP_200_OK,
    summary="List available tenants",
)
def list_available_tenants(
    principal: AuthenticatedPrincipalDependency,
    session: DatabaseSessionDependency,
    service: ListAvailableTenantsServiceDependency,
) -> TenantListResponse:
    """List tenants backed by the caller's active memberships."""

    results = service.execute(
        session,
        ListAvailableTenantsCommand(
            user_id=principal.user_id,
        ),
    )

    return TenantListResponse(items=[_tenant_summary_response(result) for result in results])


@router.get(
    "/{tenant_id}",
    response_model=TenantDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get tenant details",
)
def get_tenant_details(
    context: TenantReadAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: GetTenantDetailsServiceDependency,
) -> TenantDetailResponse:
    """Return one tenant after current persisted authorization."""

    result = service.execute(
        session,
        GetTenantDetailsCommand(
            tenant_id=context.tenant_id,
        ),
    )

    return _tenant_detail_response(result, context)


@router.get(
    "/{tenant_id}/memberships",
    response_model=MembershipListResponse,
    status_code=status.HTTP_200_OK,
    summary="List tenant memberships",
)
def list_tenant_memberships(
    context: MemberReadAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: ListTenantMembershipsServiceDependency,
) -> MembershipListResponse:
    """List memberships after current persisted authorization."""

    results = service.execute(
        session,
        ListTenantMembershipsCommand(
            tenant_id=context.tenant_id,
        ),
    )

    return MembershipListResponse(items=[_membership_response(result) for result in results])
