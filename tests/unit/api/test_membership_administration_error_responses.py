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
from clinicops.tenancy.exceptions import (
    InvalidOwnershipTransferError,
    MembershipActorNotAuthorizedError,
    MembershipAlreadyActiveError,
    MembershipAlreadyDisabledError,
    MembershipDisabledError,
    MembershipNotFoundError,
    MembershipOwnerProtectedError,
    MembershipRoleNotAllowedError,
    MembershipSelfManagementNotAllowedError,
    TenantDisabledError,
    TenantNotFoundError,
    TenantOwnershipConflictError,
)


@pytest.mark.parametrize(
    (
        "exception",
        "expected_status",
        "expected_title",
    ),
    [
        (
            MembershipNotFoundError(),
            404,
            "Resource not found",
        ),
        (
            TenantNotFoundError(),
            404,
            "Resource not found",
        ),
        (
            MembershipActorNotAuthorizedError(),
            403,
            "Operation forbidden",
        ),
        (
            TenantDisabledError(),
            403,
            "Operation forbidden",
        ),
        (
            MembershipRoleNotAllowedError(),
            400,
            "Application request failed",
        ),
        (
            MembershipDisabledError(),
            409,
            "Resource conflict",
        ),
        (
            MembershipOwnerProtectedError(),
            409,
            "Resource conflict",
        ),
        (
            MembershipSelfManagementNotAllowedError(),
            409,
            "Resource conflict",
        ),
        (
            MembershipAlreadyDisabledError(),
            409,
            "Resource conflict",
        ),
        (
            MembershipAlreadyActiveError(),
            409,
            "Resource conflict",
        ),
        (
            TenantOwnershipConflictError(),
            409,
            "Resource conflict",
        ),
        (
            InvalidOwnershipTransferError(),
            409,
            "Resource conflict",
        ),
    ],
)
def test_membership_administration_failures_use_stable_problem_details(
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
    assert "www-authenticate" not in response.headers


@pytest.mark.parametrize(
    "exception",
    [
        MembershipActorNotAuthorizedError(),
        MembershipOwnerProtectedError(),
        TenantOwnershipConflictError(),
    ],
)
def test_membership_administration_failures_do_not_claim_bearer_failure(
    exception: ApplicationError,
) -> None:
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)

    @application.post("/membership-operation")
    def mutate_membership() -> None:
        raise exception

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.post("/membership-operation")

    assert 400 <= response.status_code < 500
    assert "www-authenticate" not in response.headers
