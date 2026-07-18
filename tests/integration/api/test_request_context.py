from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from clinicops.core.request_context import get_request_context


def assert_valid_uuid(value: str) -> None:
    assert str(UUID(value)) == value


def test_missing_identifiers_are_generated(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    request_id = response.headers[REQUEST_ID_HEADER]
    correlation_id = response.headers[CORRELATION_ID_HEADER]

    assert_valid_uuid(request_id)
    assert correlation_id == request_id


def test_valid_identifiers_are_preserved_in_canonical_form(
    client: TestClient,
) -> None:
    request_id = str(uuid4()).upper()
    correlation_id = str(uuid4()).upper()

    response = client.get(
        "/api/v1/health/live",
        headers={
            REQUEST_ID_HEADER: request_id,
            CORRELATION_ID_HEADER: correlation_id,
        },
    )

    assert response.headers[REQUEST_ID_HEADER] == request_id.lower()
    assert response.headers[CORRELATION_ID_HEADER] == correlation_id.lower()


def test_invalid_identifiers_are_replaced(client: TestClient) -> None:
    response = client.get(
        "/api/v1/health/live",
        headers={
            REQUEST_ID_HEADER: "invalid-request-id",
            CORRELATION_ID_HEADER: "invalid-correlation-id",
        },
    )

    request_id = response.headers[REQUEST_ID_HEADER]
    correlation_id = response.headers[CORRELATION_ID_HEADER]

    assert_valid_uuid(request_id)
    assert correlation_id == request_id


def test_request_context_is_available_to_downstream_handlers() -> None:
    application = FastAPI()
    application.add_middleware(RequestContextMiddleware)

    @application.get("/context")
    def read_context() -> dict[str, str | None]:
        context = get_request_context()

        return {
            "request_id": context.request_id if context is not None else None,
            "correlation_id": context.correlation_id if context is not None else None,
        }

    request_id = str(uuid4())
    correlation_id = str(uuid4())

    with TestClient(application) as test_client:
        response = test_client.get(
            "/context",
            headers={
                REQUEST_ID_HEADER: request_id,
                CORRELATION_ID_HEADER: correlation_id,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "request_id": request_id,
        "correlation_id": correlation_id,
    }
