import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinicops.api.errors import (
    PROBLEM_MEDIA_TYPE,
    register_exception_handlers,
)
from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from clinicops.core.exceptions import ApplicationError
from clinicops.invitations.exceptions import (
    InvitationActorNotAuthorizedError,
    InvitationAlreadyAcceptedError,
    InvitationAlreadyPendingError,
    InvitationExpiredError,
    InvitationIssuerNotAuthorizedError,
    InvitationMembershipAlreadyExistsError,
    InvitationNotFoundError,
    InvitationRevokedError,
    InvitationRoleNotAllowedError,
)
from clinicops.tenancy.exceptions import (
    TenantDisabledError,
    TenantNotFoundError,
)


@pytest.mark.parametrize(
    (
        "exception",
        "expected_status",
        "expected_title",
    ),
    [
        (
            InvitationNotFoundError(),
            404,
            "Resource not found",
        ),
        (
            InvitationIssuerNotAuthorizedError(),
            403,
            "Operation forbidden",
        ),
        (
            InvitationActorNotAuthorizedError(),
            403,
            "Operation forbidden",
        ),
        (
            InvitationAlreadyPendingError(),
            409,
            "Resource conflict",
        ),
        (
            InvitationMembershipAlreadyExistsError(),
            409,
            "Resource conflict",
        ),
        (
            InvitationAlreadyAcceptedError(),
            409,
            "Resource conflict",
        ),
        (
            InvitationRevokedError(),
            409,
            "Resource conflict",
        ),
        (
            InvitationExpiredError(),
            409,
            "Resource conflict",
        ),
        (
            InvitationRoleNotAllowedError(),
            400,
            "Application request failed",
        ),
        (
            TenantNotFoundError(),
            404,
            "Resource not found",
        ),
        (
            TenantDisabledError(),
            403,
            "Operation forbidden",
        ),
    ],
)
def test_invitation_application_failures_use_stable_problem_details(
    exception: ApplicationError,
    expected_status: int,
    expected_title: str,
) -> None:
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)

    @application.get("/failure")
    def fail() -> None:
        raise exception

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/failure")

    body = response.json()

    assert response.status_code == expected_status
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["type"] == (f"urn:clinicops:problem:{exception.code}")
    assert body["title"] == expected_title
    assert body["status"] == expected_status
    assert body["detail"] == exception.public_message
    assert body["code"] == exception.code
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert body["correlation_id"] == response.headers[CORRELATION_ID_HEADER]
