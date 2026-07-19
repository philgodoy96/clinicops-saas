from collections.abc import Iterator, Mapping
from typing import Protocol
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinicops.api.errors import register_exception_handlers
from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
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


def assert_trace_identifiers(
    response_body: dict[str, object],
    response: TraceResponse,
) -> None:
    request_id = response.headers[REQUEST_ID_HEADER]
    correlation_id = response.headers[CORRELATION_ID_HEADER]

    assert str(UUID(request_id)) == request_id
    assert str(UUID(correlation_id)) == correlation_id

    error = response_body["error"]
    assert isinstance(error, dict)
    assert error["request_id"] == request_id
    assert error["correlation_id"] == correlation_id


def test_application_error_uses_standard_response(
    error_client: TestClient,
) -> None:
    response = error_client.get("/application-error")
    body = response.json()

    assert response.status_code == 400
    assert body["error"]["code"] == "demo_error"
    assert body["error"]["message"] == ("The demo operation could not be completed.")
    assert "internal application detail" not in response.text
    assert_trace_identifiers(body, response)


def test_validation_error_excludes_raw_input(
    error_client: TestClient,
) -> None:
    response = error_client.get("/validation/not-an-integer")
    body = response.json()

    assert response.status_code == 422
    assert body["error"]["code"] == "request_validation_error"
    assert body["error"]["message"] == "The request could not be validated."
    details = body["error"]["details"]
    assert len(details) == 1
    assert details[0]["location"] == ["path", "item_id"]
    assert details[0]["type"] == "int_parsing"
    assert details[0]["message"]
    assert "input" not in details[0]
    assert_trace_identifiers(body, response)


def test_not_found_uses_standard_response(
    error_client: TestClient,
) -> None:
    response = error_client.get("/missing")
    body = response.json()

    assert response.status_code == 404
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "The requested resource was not found."
    assert_trace_identifiers(body, response)


def test_method_not_allowed_preserves_framework_headers(
    error_client: TestClient,
) -> None:
    response = error_client.post("/validation/1")
    body = response.json()

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert body["error"]["code"] == "method_not_allowed"
    assert_trace_identifiers(body, response)


def test_unexpected_exception_uses_generic_response(
    error_client: TestClient,
) -> None:
    response = error_client.get("/unexpected")
    body = response.json()

    assert response.status_code == 500
    assert body["error"]["code"] == "internal_server_error"
    assert body["error"]["message"] == "An unexpected error occurred."
    assert "sensitive internal exception detail" not in response.text
    assert_trace_identifiers(body, response)
