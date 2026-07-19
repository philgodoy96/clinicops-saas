import json
import logging

from clinicops.core.logging import JsonFormatter, RequestContextFilter
from clinicops.core.request_context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)


def create_log_record(message: str = "Test message") -> logging.LogRecord:
    """Create a minimal log record for formatter and filter tests."""

    return logging.LogRecord(
        name="clinicops.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_request_context_filter_injects_active_identifiers() -> None:
    context = RequestContext(
        request_id="request-123",
        correlation_id="correlation-456",
    )
    token = bind_request_context(context)

    try:
        record = create_log_record()

        assert RequestContextFilter().filter(record) is True
    finally:
        reset_request_context(token)

    assert record.__dict__["request_id"] == "request-123"
    assert record.__dict__["correlation_id"] == "correlation-456"


def test_request_context_filter_uses_none_outside_request_context() -> None:
    record = create_log_record()

    assert RequestContextFilter().filter(record) is True
    assert record.__dict__["request_id"] is None
    assert record.__dict__["correlation_id"] is None


def test_json_formatter_serializes_supported_structured_fields() -> None:
    record = create_log_record("HTTP request finished")
    record.__dict__.update(
        {
            "event": "request_finished",
            "request_id": "request-123",
            "correlation_id": "correlation-456",
            "http_method": "GET",
            "http_path": "/api/v1/health/live",
            "status_code": 200,
            "duration_ms": 1.234,
            "unsupported_field": "must-not-be-serialized",
        }
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "clinicops.test"
    assert payload["message"] == "HTTP request finished"
    assert payload["event"] == "request_finished"
    assert payload["request_id"] == "request-123"
    assert payload["correlation_id"] == "correlation-456"
    assert payload["http_method"] == "GET"
    assert payload["http_path"] == "/api/v1/health/live"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 1.234
    assert "timestamp" in payload
    assert "unsupported_field" not in payload
