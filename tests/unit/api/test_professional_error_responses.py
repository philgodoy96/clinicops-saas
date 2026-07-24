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
from clinicops.professionals.exceptions import (
    ProfessionalAlreadyArchivedError,
    ProfessionalAlreadyLinkedError,
    ProfessionalExternalReferenceConflictError,
    ProfessionalInvalidCursorError,
    ProfessionalInvalidRegistrationError,
    ProfessionalInvalidUpdateError,
    ProfessionalMembershipInactiveError,
    ProfessionalMembershipLinkConflictError,
    ProfessionalMembershipNotFoundError,
    ProfessionalNotArchivedError,
    ProfessionalNotFoundError,
    ProfessionalNotLinkedError,
    ProfessionalVersionConflictError,
)

_SENSITIVE_INTERNAL = (
    "SELECT * FROM professionals "
    "WHERE email = 'jordan@example.com' "
    "AND registration_number = 'CRM-48291' "
    "AND tenant_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'"
)


def _error_app(
    exception: Exception,
) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)

    @application.get("/failure")
    def raise_failure() -> None:
        raise exception

    return application


@pytest.mark.parametrize(
    ("exception", "expected_status", "expected_title"),
    [
        (
            ProfessionalInvalidCursorError(),
            400,
            "Application request failed",
        ),
        (
            ProfessionalInvalidRegistrationError(),
            400,
            "Application request failed",
        ),
        (
            ProfessionalInvalidUpdateError(),
            400,
            "Application request failed",
        ),
        (
            ProfessionalNotFoundError(),
            404,
            "Resource not found",
        ),
        (
            ProfessionalMembershipNotFoundError(),
            404,
            "Resource not found",
        ),
        (
            ProfessionalAlreadyArchivedError(),
            409,
            "Resource conflict",
        ),
        (
            ProfessionalNotArchivedError(),
            409,
            "Resource conflict",
        ),
        (
            ProfessionalVersionConflictError(),
            409,
            "Resource conflict",
        ),
        (
            ProfessionalExternalReferenceConflictError(),
            409,
            "Resource conflict",
        ),
        (
            ProfessionalAlreadyLinkedError(),
            409,
            "Resource conflict",
        ),
        (
            ProfessionalNotLinkedError(),
            409,
            "Resource conflict",
        ),
        (
            ProfessionalMembershipInactiveError(),
            409,
            "Resource conflict",
        ),
        (
            ProfessionalMembershipLinkConflictError(),
            409,
            "Resource conflict",
        ),
    ],
)
def test_professional_error_uses_problem_details(
    exception: ApplicationError,
    expected_status: int,
    expected_title: str,
) -> None:
    with TestClient(
        _error_app(exception),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/failure")

    payload = response.json()

    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert payload["type"] == f"urn:clinicops:problem:{exception.code}"
    assert payload["title"] == expected_title
    assert payload["status"] == expected_status
    assert payload["detail"] == exception.public_message
    assert payload["code"] == exception.code
    assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert payload["correlation_id"] == response.headers[CORRELATION_ID_HEADER]


@pytest.mark.parametrize(
    "exception",
    [
        ProfessionalInvalidRegistrationError(_SENSITIVE_INTERNAL),
        ProfessionalNotFoundError(_SENSITIVE_INTERNAL),
        ProfessionalExternalReferenceConflictError(_SENSITIVE_INTERNAL),
        ProfessionalMembershipLinkConflictError(_SENSITIVE_INTERNAL),
    ],
)
def test_professional_error_response_hides_sensitive_internal_detail(
    exception: ApplicationError,
) -> None:
    with TestClient(
        _error_app(exception),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/failure")

    payload = response.json()

    assert payload["detail"] == exception.public_message
    assert payload["request_id"]
    assert payload["correlation_id"]
    assert "SELECT" not in response.text
    assert "jordan@example.com" not in response.text
    assert "CRM-48291" not in response.text
    assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" not in response.text
