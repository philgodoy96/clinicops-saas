from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from clinicops.invitations.models import InvitationStatus
from clinicops.tenancy.models import (
    MembershipStatus,
    TenantRole,
    TenantStatus,
)

TenantName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=120,
    ),
]
InvitedEmail = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=320,
    ),
]
InvitableTenantRole = Literal[
    TenantRole.ADMIN,
    TenantRole.STAFF,
]
ManageableTenantRole = Literal[
    TenantRole.ADMIN,
    TenantRole.STAFF,
]


class CurrentMembershipResponse(BaseModel):
    """Current caller membership within one available tenant."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    id: UUID
    role: TenantRole
    status: MembershipStatus


class TenantSummaryResponse(BaseModel):
    """Tenant available to the current authenticated user."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    id: UUID
    name: TenantName
    status: TenantStatus
    current_membership: CurrentMembershipResponse


class TenantListResponse(BaseModel):
    """Collection of tenants available to the current user."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    items: list[TenantSummaryResponse]


class TenantDetailResponse(BaseModel):
    """Detailed tenant representation with current caller access."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    id: UUID
    name: TenantName
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None
    current_membership: CurrentMembershipResponse


class MembershipResponse(BaseModel):
    """Public tenant membership representation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None


class MembershipListResponse(BaseModel):
    """Collection of memberships within one tenant."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    items: list[MembershipResponse]


class InvitationResponse(BaseModel):
    """Public tenant invitation representation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    id: UUID
    tenant_id: UUID
    invited_email: InvitedEmail
    role: TenantRole
    status: InvitationStatus
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InvitationListResponse(BaseModel):
    """Collection of invitations within one tenant."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    items: list[InvitationResponse]


class IssueInvitationRequest(BaseModel):
    """Request to issue one invitation for the selected tenant."""

    model_config = ConfigDict(extra="forbid")

    invited_email: InvitedEmail
    role: InvitableTenantRole


class IssuedInvitationResponse(BaseModel):
    """New invitation and its one-time plaintext token."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    id: UUID
    tenant_id: UUID
    invited_email: InvitedEmail
    role: InvitableTenantRole
    expires_at: datetime
    token: str = Field(
        min_length=1,
        repr=False,
    )


class RevokedInvitationResponse(BaseModel):
    """Public result of a successful invitation revocation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    invitation_id: UUID
    tenant_id: UUID
    revoked_at: datetime


class CreateTenantRequest(BaseModel):
    """Request to create a tenant for the authenticated user."""

    model_config = ConfigDict(extra="forbid")

    name: TenantName


class CreatedTenantResponse(BaseModel):
    """Public result of tenant creation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    id: UUID
    name: TenantName
    status: TenantStatus
    owner_user_id: UUID
    created_at: datetime


class ChangeMembershipRoleRequest(BaseModel):
    """Request to assign an administrative membership role."""

    model_config = ConfigDict(extra="forbid")

    role: ManageableTenantRole


class ChangedMembershipRoleResponse(BaseModel):
    """Public result of a membership role change."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    previous_role: TenantRole
    role: TenantRole
    updated_at: datetime


class DisabledMembershipResponse(BaseModel):
    """Public result of disabling a tenant membership."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    disabled_at: datetime


class EnabledMembershipResponse(BaseModel):
    """Public result of enabling a tenant membership."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    updated_at: datetime


class TransferTenantOwnershipRequest(BaseModel):
    """Request to transfer tenant ownership to an existing user."""

    model_config = ConfigDict(extra="forbid")

    new_owner_user_id: UUID


class TransferredTenantOwnershipResponse(BaseModel):
    """Public result of an atomic tenant ownership transfer."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    tenant_id: UUID
    previous_owner_user_id: UUID
    new_owner_user_id: UUID
