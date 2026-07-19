from collections.abc import Mapping
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
)
from clinicops.core.exceptions import ApplicationError
from clinicops.core.request_context import get_correlation_id, get_request_id
from clinicops.db.exceptions import DatabaseUnavailableError

type ErrorLocation = str | int


class ValidationErrorDetail(BaseModel):
    """Sanitized validation failure exposed to API clients."""

    location: list[ErrorLocation]
    message: str
    type: str


class ErrorBody(BaseModel):
    """Standard public API error payload."""

    code: str
    message: str
    request_id: str
    correlation_id: str
    details: list[ValidationErrorDetail] | None = None


class ErrorResponse(BaseModel):
    """Top-level API error response."""

    error: ErrorBody


HTTP_ERROR_DEFINITIONS: dict[int, tuple[str, str]] = {
    status.HTTP_400_BAD_REQUEST: (
        "bad_request",
        "The request could not be processed.",
    ),
    status.HTTP_401_UNAUTHORIZED: (
        "unauthorized",
        "Authentication is required.",
    ),
    status.HTTP_403_FORBIDDEN: (
        "forbidden",
        "The requested operation is not allowed.",
    ),
    status.HTTP_404_NOT_FOUND: (
        "not_found",
        "The requested resource was not found.",
    ),
    status.HTTP_405_METHOD_NOT_ALLOWED: (
        "method_not_allowed",
        "The HTTP method is not allowed for this resource.",
    ),
    status.HTTP_409_CONFLICT: (
        "conflict",
        "The request conflicts with the current resource state.",
    ),
    status.HTTP_429_TOO_MANY_REQUESTS: (
        "rate_limit_exceeded",
        "Too many requests were received.",
    ),
}


def _trace_identifiers(request: Request) -> tuple[str, str]:
    """Resolve trace identifiers from request state or active context."""

    request_id = getattr(request.state, "request_id", None) or get_request_id() or str(uuid4())
    correlation_id = (
        getattr(request.state, "correlation_id", None) or get_correlation_id() or request_id
    )
    return request_id, correlation_id


def _response_headers(
    request_id: str,
    correlation_id: str,
    additional_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge trace headers with optional framework-provided headers."""

    headers = dict(additional_headers or {})
    headers[REQUEST_ID_HEADER] = request_id
    headers[CORRELATION_ID_HEADER] = correlation_id
    return headers


def _error_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[ValidationErrorDetail] | None = None,
    additional_headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the standard public API error response."""

    request_id, correlation_id = _trace_identifiers(request)
    payload = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id,
            correlation_id=correlation_id,
            details=details,
        )
    )

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
        headers=_response_headers(
            request_id,
            correlation_id,
            additional_headers,
        ),
    )


def _application_error_status(exception: ApplicationError) -> int:
    """Map application failures to transport status codes."""

    if isinstance(exception, DatabaseUnavailableError):
        return status.HTTP_503_SERVICE_UNAVAILABLE

    return status.HTTP_400_BAD_REQUEST


async def application_error_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Render an expected application failure."""

    assert isinstance(exception, ApplicationError)

    return _error_response(
        request=request,
        status_code=_application_error_status(exception),
        code=exception.code,
        message=exception.public_message,
    )


async def request_validation_error_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Render a sanitized request validation failure."""

    assert isinstance(exception, RequestValidationError)

    details = [
        ValidationErrorDetail(
            location=list(error["loc"]),
            message=str(error["msg"]),
            type=str(error["type"]),
        )
        for error in exception.errors()
    ]

    return _error_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="request_validation_error",
        message="The request could not be validated.",
        details=details,
    )


async def http_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Render framework and route-level HTTP failures."""

    assert isinstance(exception, StarletteHTTPException)

    code, message = HTTP_ERROR_DEFINITIONS.get(
        exception.status_code,
        (
            "http_error",
            "The request could not be completed.",
        ),
    )

    return _error_response(
        request=request,
        status_code=exception.status_code,
        code=code,
        message=message,
        additional_headers=exception.headers,
    )


async def unhandled_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Render a generic response for unexpected failures."""

    return _error_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_server_error",
        message="An unexpected error occurred.",
    )


def register_exception_handlers(application: FastAPI) -> None:
    """Register the ClinicOps API exception handlers."""

    application.add_exception_handler(
        ApplicationError,
        application_error_handler,
    )
    application.add_exception_handler(
        RequestValidationError,
        request_validation_error_handler,
    )
    application.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,
    )
    application.add_exception_handler(
        Exception,
        unhandled_exception_handler,
    )
