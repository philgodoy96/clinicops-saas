from uuid import UUID, uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from clinicops.core.request_context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


def _normalize_uuid(value: str | None) -> str | None:
    """Return a canonical UUID string when the input is valid."""

    if value is None:
        return None

    try:
        return str(UUID(value))
    except ValueError:
        return None


def _resolve_request_id(value: str | None) -> str:
    """Preserve a valid request identifier or generate a new UUID."""

    return _normalize_uuid(value) or str(uuid4())


def _resolve_correlation_id(value: str | None, request_id: str) -> str:
    """Preserve a valid correlation identifier or reuse the request identifier."""

    return _normalize_uuid(value) or request_id


class RequestContextMiddleware:
    """Bind request identifiers and expose them on every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = _resolve_request_id(headers.get(REQUEST_ID_HEADER))
        correlation_id = _resolve_correlation_id(
            headers.get(CORRELATION_ID_HEADER),
            request_id,
        )
        context = RequestContext(
            request_id=request_id,
            correlation_id=correlation_id,
        )
        token = bind_request_context(context)

        async def send_with_context_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id
                response_headers[CORRELATION_ID_HEADER] = correlation_id

            await send(message)

        try:
            await self.app(scope, receive, send_with_context_headers)
        finally:
            reset_request_context(token)
