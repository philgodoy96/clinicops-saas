from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from clinicops.api.dependencies import DatabaseSessionDependency
from clinicops.api.v1.authentication.dependencies import (
    AuthenticatedPrincipalDependency,
)
from clinicops.api.v1.tenants.dependencies import (
    ChangeMembershipRoleServiceDependency,
    CreateTenantServiceDependency,
    DisableMembershipServiceDependency,
    EnableMembershipServiceDependency,
    GetTenantDetailsServiceDependency,
    IssueInvitationServiceDependency,
    ListAvailableTenantsServiceDependency,
    ListTenantInvitationsServiceDependency,
    ListTenantMembershipsServiceDependency,
    RemoveMembershipServiceDependency,
    RevokeInvitationServiceDependency,
    TransferTenantOwnershipServiceDependency,
    require_tenant_permission,
)
from clinicops.api.v1.tenants.schemas import (
    ChangedMembershipRoleResponse,
    ChangeMembershipRoleRequest,
    CreatedTenantResponse,
    CreateTenantRequest,
    CurrentMembershipResponse,
    DisabledMembershipResponse,
    EnabledMembershipResponse,
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
    TransferredTenantOwnershipResponse,
    TransferTenantOwnershipRequest,
)
from clinicops.audit.context import AuditRecordingContext
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.core.request_context import (
    get_correlation_id,
    get_request_id,
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
from clinicops.tenancy.models import MembershipStatus, TenantRole
from clinicops.tenancy.services.create_tenant import (
    CreateTenantCommand,
)
from clinicops.tenancy.services.membership_administration import (
    ChangeMembershipRoleCommand,
    DisableMembershipCommand,
    EnableMembershipCommand,
    RemoveMembershipCommand,
)
from clinicops.tenancy.services.queries import (
    AvailableTenant,
    GetTenantDetailsCommand,
    ListAvailableTenantsCommand,
    ListTenantMembershipsCommand,
    TenantDetails,
    TenantMembership,
)
from clinicops.tenancy.services.transfer_ownership import (
    TransferTenantOwnershipCommand,
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
MemberManageAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(
        require_tenant_permission(
            TenantPermission.MEMBER_MANAGE,
        )
    ),
]
OwnershipTransferAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(
        require_tenant_permission(
            TenantPermission.OWNERSHIP_TRANSFER,
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


def _http_audit_context(
    *,
    user_id: UUID,
    role: str,
) -> AuditRecordingContext:
    """Build audit attribution from trusted runtime request state."""

    request_id = get_request_id()
    correlation_id = get_correlation_id()

    if request_id is None or correlation_id is None:
        raise RuntimeError(
            "Request context identifiers are required for audit recording.",
        )

    return AuditRecordingContext.http_user(
        user_id=user_id,
        role=role,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post(
    "",
    response_model=CreatedTenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant",
)
def create_tenant(
    payload: CreateTenantRequest,
    principal: AuthenticatedPrincipalDependency,
    session: DatabaseSessionDependency,
    service: CreateTenantServiceDependency,
) -> CreatedTenantResponse:
    """Create a tenant with the authenticated user as owner."""

    result = service.execute(
        session,
        CreateTenantCommand(
            name=payload.name,
            owner_user_id=principal.user_id,
            audit_context=_http_audit_context(
                user_id=principal.user_id,
                role=TenantRole.OWNER.value,
            ),
        ),
    )
    session.commit()

    return CreatedTenantResponse.model_validate(result)


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


@router.patch(
    "/{tenant_id}/memberships/{membership_id}/role",
    response_model=ChangedMembershipRoleResponse,
    status_code=status.HTTP_200_OK,
    summary="Change a tenant membership role",
)
def change_tenant_membership_role(
    payload: ChangeMembershipRoleRequest,
    membership_id: UUID,
    context: MemberManageAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: ChangeMembershipRoleServiceDependency,
) -> ChangedMembershipRoleResponse:
    """Change a non-owner membership role and commit."""

    result = service.execute(
        session,
        ChangeMembershipRoleCommand(
            tenant_id=context.tenant_id,
            actor_user_id=context.user_id,
            membership_id=membership_id,
            role=payload.role,
            audit_context=_http_audit_context(
                user_id=context.user_id,
                role=context.role.value,
            ),
        ),
    )
    session.commit()

    return ChangedMembershipRoleResponse.model_validate(result)


@router.post(
    "/{tenant_id}/memberships/{membership_id}/disable",
    response_model=DisabledMembershipResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable a tenant membership",
)
def disable_tenant_membership(
    membership_id: UUID,
    context: MemberManageAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: DisableMembershipServiceDependency,
) -> DisabledMembershipResponse:
    """Disable a non-owner membership and commit."""

    result = service.execute(
        session,
        DisableMembershipCommand(
            tenant_id=context.tenant_id,
            actor_user_id=context.user_id,
            membership_id=membership_id,
        ),
    )
    session.commit()

    return DisabledMembershipResponse.model_validate(result)


@router.post(
    "/{tenant_id}/memberships/{membership_id}/enable",
    response_model=EnabledMembershipResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable a tenant membership",
)
def enable_tenant_membership(
    membership_id: UUID,
    context: MemberManageAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: EnableMembershipServiceDependency,
) -> EnabledMembershipResponse:
    """Enable a disabled non-owner membership and commit."""

    result = service.execute(
        session,
        EnableMembershipCommand(
            tenant_id=context.tenant_id,
            actor_user_id=context.user_id,
            membership_id=membership_id,
        ),
    )
    session.commit()

    return EnabledMembershipResponse.model_validate(result)


@router.delete(
    "/{tenant_id}/memberships/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a tenant membership",
)
def remove_tenant_membership(
    membership_id: UUID,
    context: MemberManageAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: RemoveMembershipServiceDependency,
) -> Response:
    """Remove a non-owner membership and commit."""

    service.execute(
        session,
        RemoveMembershipCommand(
            tenant_id=context.tenant_id,
            actor_user_id=context.user_id,
            membership_id=membership_id,
            audit_context=_http_audit_context(
                user_id=context.user_id,
                role=context.role.value,
            ),
        ),
    )
    session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{tenant_id}/ownership/transfer",
    response_model=TransferredTenantOwnershipResponse,
    status_code=status.HTTP_200_OK,
    summary="Transfer tenant ownership",
)
def transfer_tenant_ownership(
    payload: TransferTenantOwnershipRequest,
    context: OwnershipTransferAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: TransferTenantOwnershipServiceDependency,
) -> TransferredTenantOwnershipResponse:
    """Transfer ownership atomically and commit."""

    result = service.execute(
        session,
        TransferTenantOwnershipCommand(
            tenant_id=context.tenant_id,
            expected_current_owner_user_id=context.user_id,
            new_owner_user_id=payload.new_owner_user_id,
            audit_context=_http_audit_context(
                user_id=context.user_id,
                role=context.role.value,
            ),
        ),
    )
    session.commit()

    return TransferredTenantOwnershipResponse.model_validate(result)


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
