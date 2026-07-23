from fastapi import APIRouter, status

from clinicops.api.dependencies import DatabaseSessionDependency
from clinicops.api.v1.invitations.dependencies import (
    AcceptInvitationServiceDependency,
)
from clinicops.api.v1.invitations.schemas import (
    AcceptedInvitationResponse,
    AcceptInvitationRequest,
)
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import AuditActor
from clinicops.audit.enums import AuditSource
from clinicops.core.request_context import (
    get_correlation_id,
    get_request_id,
)
from clinicops.invitations.services.accept_invitation import (
    AcceptInvitationCommand,
)

router = APIRouter(
    prefix="/invitations",
    tags=["invitations"],
)


def _http_system_audit_context() -> AuditRecordingContext:
    """Build system audit attribution for public invitation acceptance."""

    request_id = get_request_id()
    correlation_id = get_correlation_id()

    if request_id is None or correlation_id is None:
        raise RuntimeError(
            "Request context identifiers are required for audit recording.",
        )

    return AuditRecordingContext(
        actor=AuditActor.system(),
        source=AuditSource.HTTP,
        request_id=request_id,
        correlation_id=correlation_id,
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
            audit_context=_http_system_audit_context(),
        ),
    )
    session.commit()

    return AcceptedInvitationResponse.model_validate(result)
