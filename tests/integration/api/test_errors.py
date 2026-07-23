import logging
from collections.abc import Iterator, Mapping
from typing import Protocol
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, status
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
from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)
from clinicops.audit.policies import (
    AuditLogAccessDeniedError,
)
from clinicops.authentication.exceptions import InvalidCredentialsError
from clinicops.authorization.exceptions import (
    TenantNotFoundError,
    TenantPermissionDeniedError,
)
from clinicops.core.exceptions import ApplicationError


class TraceResponse(Protocol):
    """Response shape required by trace assertion helpers."""

    @property
    def headers(self) -> Mapping[str, str]:
        """Return response headers."""
        ...


class DemoApplicationError(ApplicationError):
    """Expected failure used to verify application error mapping."""

    code = "demo_error"
    public_message = "The demo operation could not be completed."


def create_error_test_app() -> FastAPI:
    """Create an isolated application with error-producing endpoints."""

    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)

    @application.get("/application-error")
    def raise_application_error() -> None:
        raise DemoApplicationError("internal application detail")

    @application.get("/invalid-credentials")
    def raise_invalid_credentials() -> None:
        raise InvalidCredentialsError()

    @application.get("/tenant-not-found")
    def raise_tenant_not_found() -> None:
        raise TenantNotFoundError()

    @application.get("/permission-denied")
    def raise_permission_denied() -> None:
        raise TenantPermissionDeniedError()

    @application.get("/audit-log-invalid-configuration")
    def raise_audit_log_invalid_configuration() -> None:
        raise AuditLogInvalidConfigurationError("sensitive internal detail")

    @application.get("/audit-log-access-denied")
    def raise_audit_log_access_denied() -> None:
        raise AuditLogAccessDeniedError()

    @application.get("/http-unauthorized")
    def raise_http_unauthorized() -> None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    @application.get("/validation/{item_id}")
    def read_validated_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @application.get("/unexpected")
    def raise_unexpected_error() -> None:
        raise RuntimeError("sensitive internal exception detail")

    return application


@pytest.fixture
def error_client() -> Iterator[TestClient]:
    with TestClient(
        create_error_test_app(),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


def assert_problem_content_type(response: TraceResponse) -> None:
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE


def assert_trace_identifiers(
    response_body: dict[str, object],
    response: TraceResponse,
) -> None:
    request_id = response.headers[REQUEST_ID_HEADER]
    correlation_id = response.headers[CORRELATION_ID_HEADER]

    assert str(UUID(request_id)) == request_id
    assert str(UUID(correlation_id)) == correlation_id
    assert response_body["request_id"] == request_id
    assert response_body["correlation_id"] == correlation_id


def assert_problem_identity(
    body: dict[str, object],
    *,
    status_code: int,
    code: str,
) -> None:
    assert body["type"] == f"urn:clinicops:problem:{code}"
    assert body["status"] == status_code
    assert body["code"] == code


def test_application_error_uses_problem_details(
    error_client: TestClient,
) -> None:
    response = error_client.get("/application-error")
    body = response.json()

    assert response.status_code == 400
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=400,
        code="demo_error",
    )
    assert body["title"] == "Application request failed"
    assert body["detail"] == ("The demo operation could not be completed.")
    assert "internal application detail" not in response.text
    assert "error" not in body
    assert_trace_identifiers(body, response)


def test_authentication_error_maps_to_bearer_unauthorized(
    error_client: TestClient,
) -> None:
    response = error_client.get("/invalid-credentials")
    body = response.json()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=401,
        code="invalid_credentials",
    )
    assert body["title"] == "Authentication failed"
    assert body["detail"] == "The email or password is invalid."
    assert_trace_identifiers(body, response)


def test_tenant_not_found_maps_to_not_found(
    error_client: TestClient,
) -> None:
    response = error_client.get("/tenant-not-found")
    body = response.json()

    assert response.status_code == 404
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=404,
        code="tenant_not_found",
    )
    assert body["title"] == "Resource not found"
    assert body["detail"] == "The tenant was not found."
    assert_trace_identifiers(body, response)


def test_permission_denial_maps_to_forbidden(
    error_client: TestClient,
) -> None:
    response = error_client.get("/permission-denied")
    body = response.json()

    assert response.status_code == 403
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=403,
        code="tenant_permission_denied",
    )
    assert body["title"] == "Operation forbidden"
    assert body["detail"] == ("The tenant operation is not permitted.")
    assert_trace_identifiers(body, response)


def test_audit_log_invalid_configuration_maps_to_bad_request(
    error_client: TestClient,
) -> None:
    response = error_client.get("/audit-log-invalid-configuration")
    body = response.json()

    assert response.status_code == 400
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=400,
        code="audit_log_invalid_configuration",
    )
    assert body["title"] == "Application request failed"
    assert body["detail"] == ("The audit log request is invalid.")
    assert "sensitive internal detail" not in response.text
    assert_trace_identifiers(body, response)


def test_audit_log_access_denied_maps_to_forbidden(
    error_client: TestClient,
) -> None:
    response = error_client.get("/audit-log-access-denied")
    body = response.json()

    assert response.status_code == 403
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=403,
        code="audit_log_access_denied",
    )
    assert body["title"] == "Operation forbidden"
    assert body["detail"] == ("The current membership cannot read tenant audit logs.")
    assert_trace_identifiers(body, response)


def test_validation_error_excludes_raw_input(
    error_client: TestClient,
) -> None:
    response = error_client.get("/validation/not-an-integer")
    body = response.json()

    assert response.status_code == 422
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=422,
        code="request_validation_error",
    )
    assert body["title"] == "Request validation failed"
    assert body["detail"] == "The request could not be validated."

    errors = body["errors"]

    assert isinstance(errors, list)
    assert len(errors) == 1
    assert errors[0]["location"] == ["path", "item_id"]
    assert errors[0]["type"] == "int_parsing"
    assert errors[0]["message"]
    assert "input" not in errors[0]
    assert_trace_identifiers(body, response)


def test_not_found_uses_problem_details(
    error_client: TestClient,
) -> None:
    response = error_client.get("/missing")
    body = response.json()

    assert response.status_code == 404
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=404,
        code="not_found",
    )
    assert body["title"] == "Resource not found"
    assert body["detail"] == ("The requested resource was not found.")
    assert_trace_identifiers(body, response)


def test_method_not_allowed_preserves_framework_headers(
    error_client: TestClient,
) -> None:
    response = error_client.post("/validation/1")
    body = response.json()

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=405,
        code="method_not_allowed",
    )
    assert body["title"] == "Method not allowed"
    assert_trace_identifiers(body, response)


def test_http_unauthorized_adds_bearer_challenge(
    error_client: TestClient,
) -> None:
    response = error_client.get("/http-unauthorized")
    body = response.json()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=401,
        code="unauthorized",
    )
    assert body["detail"] == "Authentication is required."
    assert_trace_identifiers(body, response)


def test_unexpected_exception_is_logged_and_sanitized(
    error_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(
        logging.ERROR,
        logger="clinicops.http",
    ):
        response = error_client.get("/unexpected")

    body = response.json()

    assert response.status_code == 500
    assert_problem_content_type(response)
    assert_problem_identity(
        body,
        status_code=500,
        code="internal_server_error",
    )
    assert body["title"] == "Internal server error"
    assert body["detail"] == "An unexpected error occurred."
    assert "sensitive internal exception detail" not in response.text
    assert_trace_identifiers(body, response)

    unhandled_records = [
        record for record in caplog.records if record.getMessage() == "unhandled_exception"
    ]

    assert len(unhandled_records) == 1
    assert unhandled_records[0].exc_info is not None
