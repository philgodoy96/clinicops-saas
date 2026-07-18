from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Identifiers associated with one HTTP request execution."""

    request_id: str
    correlation_id: str


_request_context: ContextVar[RequestContext | None] = ContextVar(
    "clinicops_request_context",
    default=None,
)


def bind_request_context(context: RequestContext) -> Token[RequestContext | None]:
    """Bind request context to the current execution context."""

    return _request_context.set(context)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    """Restore the request context state that existed before a bind operation."""

    _request_context.reset(token)


def get_request_context() -> RequestContext | None:
    """Return the active request context when one is bound."""

    return _request_context.get()


def get_request_id() -> str | None:
    """Return the active request identifier when available."""

    context = get_request_context()
    return context.request_id if context is not None else None


def get_correlation_id() -> str | None:
    """Return the active correlation identifier when available."""

    context = get_request_context()
    return context.correlation_id if context is not None else None
