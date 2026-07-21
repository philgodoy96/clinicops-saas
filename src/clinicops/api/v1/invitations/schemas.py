from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from clinicops.tenancy.models import TenantRole

InvitationToken = Annotated[
    str,
    StringConstraints(min_length=1),
]
InvitationPassword = Annotated[
    str,
    StringConstraints(
        min_length=12,
        max_length=128,
    ),
]


class AcceptInvitationRequest(BaseModel):
    """Request to consume one invitation credential."""

    model_config = ConfigDict(extra="forbid")

    token: InvitationToken = Field(repr=False)
    password: InvitationPassword | None = Field(
        default=None,
        repr=False,
    )


class AcceptedInvitationResponse(BaseModel):
    """Public result of successful invitation acceptance."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

    invitation_id: UUID
    tenant_id: UUID
    membership_id: UUID
    user_id: UUID
    role: TenantRole
    user_was_created: bool
    accepted_at: datetime
