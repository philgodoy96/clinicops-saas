from fastapi import APIRouter, status

from clinicops.api.dependencies import DatabaseSessionDependency
from clinicops.api.v1.invitations.dependencies import (
    AcceptInvitationServiceDependency,
)
from clinicops.api.v1.invitations.schemas import (
    AcceptedInvitationResponse,
    AcceptInvitationRequest,
)
from clinicops.invitations.services.accept_invitation import (
    AcceptInvitationCommand,
)

router = APIRouter(
    prefix="/invitations",
    tags=["invitations"],
)


@router.post(
    "/accept",
    response_model=AcceptedInvitationResponse,
    status_code=status.HTTP_200_OK,
    summary="Accept a tenant invitation",
)
def accept_invitation(
    payload: AcceptInvitationRequest,
    session: DatabaseSessionDependency,
    service: AcceptInvitationServiceDependency,
) -> AcceptedInvitationResponse:
    """Accept one invitation credential and commit onboarding."""

    result = service.execute(
        session,
        AcceptInvitationCommand(
            token=payload.token,
            password=payload.password,
        ),
    )
    session.commit()

    return AcceptedInvitationResponse.model_validate(result)
