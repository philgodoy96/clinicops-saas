from typing import Annotated

from fastapi import Depends

from clinicops.invitations.services.accept_invitation import (
    AcceptInvitationService,
)


def get_accept_invitation_service() -> AcceptInvitationService:
    """Build the invitation acceptance service."""

    return AcceptInvitationService()


AcceptInvitationServiceDependency = Annotated[
    AcceptInvitationService,
    Depends(get_accept_invitation_service),
]
