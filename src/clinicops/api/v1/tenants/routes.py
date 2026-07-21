from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from clinicops.api.dependencies import DatabaseSessionDependency
from clinicops.api.v1.authentication.dependencies import (
    AuthenticatedPrincipalDependency,
)
from clinicops.api.v1.tenants.dependencies import (
    GetTenantDetailsServiceDependency,
    IssueInvitationServiceDependency,
    ListAvailableTenantsServiceDependency,
    ListTenantInvitationsServiceDependency,
    ListTenantMembershipsServiceDependency,
    RevokeInvitationServiceDependency,
    require_tenant_permission,
)
from clinicops.api.v1.tenants.schemas import (
    CurrentMembershipResponse,
    InvitationListResponse,
    InvitationResponse,
    IssuedInvitationResponse,
    IssueInvitationRequest,
    MembershipListResponse,
    MembershipResponse,
    RevokedInvitationResponse,
    TenantDetailResponse,
    TenantListResponse,
    TenantSummaryResponse,
)
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.invitations.services.issue_invitation import (
    IssueInvitationCommand,
)
from clinicops.invitations.services.queries import (
    ListTenantInvitationsCommand,
    TenantInvitation,
)
from clinicops.invitations.services.revoke_invitation import (
    RevokeInvitationCommand,
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
InvitationReadAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(
        require_tenant_permission(
            TenantPermission.INVITATION_READ,
        )
    ),
]
InvitationCreateAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(
        require_tenant_permission(
            TenantPermission.INVITATION_CREATE,
        )
    ),
]
InvitationRevokeAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(
        require_tenant_permission(
            TenantPermission.INVITATION_REVOKE,
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


def _invitation_response(
    result: TenantInvitation,
) -> InvitationResponse:
    """Translate one invitation into its public HTTP contract."""

    return InvitationResponse(
        id=result.id,
        tenant_id=result.tenant_id,
        invited_email=result.invited_email,
        role=result.role,
        status=result.status,
        expires_at=result.expires_at,
        accepted_at=result.accepted_at,
        revoked_at=result.revoked_at,
        created_at=result.created_at,
        updated_at=result.updated_at,
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


@router.get(
    "/{tenant_id}/invitations",
    response_model=InvitationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List tenant invitations",
)
def list_tenant_invitations(
    context: InvitationReadAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: ListTenantInvitationsServiceDependency,
) -> InvitationListResponse:
    """List invitations after current persisted authorization."""

    results = service.execute(
        session,
        ListTenantInvitationsCommand(
            tenant_id=context.tenant_id,
        ),
    )

    return InvitationListResponse(items=[_invitation_response(result) for result in results])


@router.post(
    "/{tenant_id}/invitations",
    response_model=IssuedInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a tenant invitation",
)
def issue_tenant_invitation(
    payload: IssueInvitationRequest,
    context: InvitationCreateAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: IssueInvitationServiceDependency,
) -> IssuedInvitationResponse:
    """Issue an invitation and commit before exposing its token."""

    result = service.execute(
        session,
        IssueInvitationCommand(
            tenant_id=context.tenant_id,
            issuer_user_id=context.user_id,
            invited_email=payload.invited_email,
            role=payload.role,
        ),
    )
    session.commit()

    return IssuedInvitationResponse.model_validate(result)


@router.post(
    "/{tenant_id}/invitations/{invitation_id}/revoke",
    response_model=RevokedInvitationResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke a tenant invitation",
)
def revoke_tenant_invitation(
    invitation_id: UUID,
    context: InvitationRevokeAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: RevokeInvitationServiceDependency,
) -> RevokedInvitationResponse:
    """Revoke an invitation and commit the lifecycle transition."""

    result = service.execute(
        session,
        RevokeInvitationCommand(
            tenant_id=context.tenant_id,
            invitation_id=invitation_id,
            actor_user_id=context.user_id,
        ),
    )
    session.commit()

    return RevokedInvitationResponse.model_validate(result)
