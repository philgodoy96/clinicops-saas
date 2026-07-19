import asyncio
import logging
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient, Response

from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from clinicops.core.logging import RequestContextFilter
from clinicops.core.request_context import RequestContext, get_request_context


class RecordingHandler(logging.Handler):
    """Collect log records emitted during concurrent requests."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def anyio_backend() -> str:
    """Run this test with the asyncio backend used by the synchronization barrier."""

    return "asyncio"


def create_isolation_test_app() -> FastAPI:
    """Create an application that forces two requests to overlap."""

    application = FastAPI()
    application.add_middleware(RequestContextMiddleware)

    started_count = 0
    started_lock = asyncio.Lock()
    release_requests = asyncio.Event()

    @application.get("/context")
    async def read_context() -> dict[str, str]:
        nonlocal started_count

        context_before_wait = get_request_context()
        assert context_before_wait is not None

        async with started_lock:
            started_count += 1
            if started_count == 2:
                release_requests.set()

        await asyncio.wait_for(release_requests.wait(), timeout=2)
        await asyncio.sleep(0)

        context_after_wait = get_request_context()
        assert context_after_wait is not None

        return {
            "request_id_before_wait": context_before_wait.request_id,
            "correlation_id_before_wait": context_before_wait.correlation_id,
            "request_id_after_wait": context_after_wait.request_id,
            "correlation_id_after_wait": context_after_wait.correlation_id,
        }

    return application


async def issue_request(
    client: AsyncClient,
    request_id: str,
    correlation_id: str,
) -> tuple[Response, RequestContext | None]:
    """Issue one request and expose any context left after completion."""

    response = await client.get(
        "/context",
        headers={
            REQUEST_ID_HEADER: request_id,
            CORRELATION_ID_HEADER: correlation_id,
        },
    )
    return response, get_request_context()


def expected_context_body(
    request_id: str,
    correlation_id: str,
) -> dict[str, str]:
    """Return the context values expected before and after the overlap point."""

    return {
        "request_id_before_wait": request_id,
        "correlation_id_before_wait": correlation_id,
        "request_id_after_wait": request_id,
        "correlation_id_after_wait": correlation_id,
    }


@pytest.mark.anyio
async def test_concurrent_requests_keep_context_and_logs_isolated() -> None:
    application = create_isolation_test_app()
    request_logger = logging.getLogger("clinicops.http")
    previous_level = request_logger.level
    handler = RecordingHandler()
    handler.addFilter(RequestContextFilter())
    request_logger.setLevel(logging.INFO)
    request_logger.addHandler(handler)

    first_request_id = str(uuid4())
    first_correlation_id = str(uuid4())
    second_request_id = str(uuid4())
    second_correlation_id = str(uuid4())

    try:
        transport = ASGITransport(app=application)

        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            first_result, second_result = await asyncio.gather(
                issue_request(
                    client,
                    first_request_id,
                    first_correlation_id,
                ),
                issue_request(
                    client,
                    second_request_id,
                    second_correlation_id,
                ),
            )
    finally:
        request_logger.removeHandler(handler)
        request_logger.setLevel(previous_level)
        handler.close()

    first_response, first_residual_context = first_result
    second_response, second_residual_context = second_result

    assert first_response.status_code == 200
    assert first_response.json() == expected_context_body(
        first_request_id,
        first_correlation_id,
    )
    assert first_response.headers[REQUEST_ID_HEADER] == first_request_id
    assert first_response.headers[CORRELATION_ID_HEADER] == first_correlation_id
    assert first_residual_context is None

    assert second_response.status_code == 200
    assert second_response.json() == expected_context_body(
        second_request_id,
        second_correlation_id,
    )
    assert second_response.headers[REQUEST_ID_HEADER] == second_request_id
    assert second_response.headers[CORRELATION_ID_HEADER] == second_correlation_id
    assert second_residual_context is None

    lifecycle_records = [
        record
        for record in handler.records
        if record.__dict__.get("event") in {"request_started", "request_finished"}
    ]

    assert {record.__dict__["request_id"] for record in lifecycle_records} == {
        first_request_id,
        second_request_id,
    }

    for request_id, correlation_id in (
        (first_request_id, first_correlation_id),
        (second_request_id, second_correlation_id),
    ):
        request_records = [
            record for record in lifecycle_records if record.__dict__["request_id"] == request_id
        ]

        assert [record.__dict__["event"] for record in request_records] == [
            "request_started",
            "request_finished",
        ]
        assert all(
            record.__dict__["correlation_id"] == correlation_id for record in request_records
        )
