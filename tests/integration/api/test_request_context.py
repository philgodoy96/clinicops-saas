import logging
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from clinicops.core.logging import RequestContextFilter
from clinicops.core.request_context import get_request_context


class RecordingHandler(logging.Handler):
    """Collect log records emitted during a test."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def request_log_records() -> Iterator[list[logging.LogRecord]]:
    request_logger = logging.getLogger("clinicops.http")
    previous_level = request_logger.level
    handler = RecordingHandler()
    handler.addFilter(RequestContextFilter())
    request_logger.setLevel(logging.INFO)
    request_logger.addHandler(handler)

    try:
        yield handler.records
    finally:
        request_logger.removeHandler(handler)
        request_logger.setLevel(previous_level)
        handler.close()


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


def test_request_lifecycle_logs_include_context(
    client: TestClient,
    request_log_records: list[logging.LogRecord],
) -> None:
    request_id = str(uuid4())
    correlation_id = str(uuid4())

    response = client.get(
        "/api/v1/health/live",
        headers={
            REQUEST_ID_HEADER: request_id,
            CORRELATION_ID_HEADER: correlation_id,
        },
    )

    assert response.status_code == 200

    lifecycle_records = [
        record
        for record in request_log_records
        if record.__dict__.get("event") in {"request_started", "request_finished"}
    ]

    assert [record.__dict__["event"] for record in lifecycle_records] == [
        "request_started",
        "request_finished",
    ]

    for record in lifecycle_records:
        assert record.__dict__["request_id"] == request_id
        assert record.__dict__["correlation_id"] == correlation_id
        assert record.__dict__["http_method"] == "GET"
        assert record.__dict__["http_path"] == "/api/v1/health/live"

    finished_record = lifecycle_records[1]
    assert finished_record.__dict__["status_code"] == 200
    assert finished_record.__dict__["duration_ms"] >= 0


def test_request_logs_exclude_query_string(
    client: TestClient,
    request_log_records: list[logging.LogRecord],
) -> None:
    response = client.get(
        "/api/v1/health/live",
        params={"email": "patient@example.com"},
    )

    assert response.status_code == 200

    for record in request_log_records:
        assert record.__dict__.get("http_path") == "/api/v1/health/live"
        assert "patient@example.com" not in record.getMessage()


def test_unhandled_exception_emits_request_failed_log(
    request_log_records: list[logging.LogRecord],
) -> None:
    application = FastAPI()
    application.add_middleware(RequestContextMiddleware)

    @application.get("/failure")
    def raise_unhandled_error() -> None:
        raise RuntimeError("unexpected failure")

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/failure")

    assert response.status_code == 500

    failed_records = [
        record for record in request_log_records if record.__dict__.get("event") == "request_failed"
    ]

    assert len(failed_records) == 1
    failed_record = failed_records[0]
    assert failed_record.__dict__["http_method"] == "GET"
    assert failed_record.__dict__["http_path"] == "/failure"
    assert failed_record.__dict__["duration_ms"] >= 0
    assert failed_record.exc_info is not None
