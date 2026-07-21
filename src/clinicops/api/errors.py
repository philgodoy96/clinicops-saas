import logging
from collections.abc import Mapping
from dataclasses import dataclass
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
from clinicops.authentication.exceptions import (
    AccessTokenExpiredError,
    AccessTokenInvalidError,
    AuthenticationSessionExpiredError,
    AuthenticationSessionInactiveError,
    AuthenticationSessionNotFoundError,
    InvalidCredentialsError,
    RefreshTokenExpiredError,
    RefreshTokenInvalidError,
)
from clinicops.authorization.exceptions import (
    TenantDisabledError,
    TenantMembershipDisabledError,
    TenantMembershipNotFoundError,
    TenantNotFoundError,
    TenantPermissionDeniedError,
)
from clinicops.core.exceptions import ApplicationError
from clinicops.core.request_context import get_correlation_id, get_request_id
from clinicops.db.exceptions import DatabaseUnavailableError
from clinicops.identity.exceptions import (
    UserDisabledError,
    UserNotFoundError,
)
from clinicops.invitations.exceptions import (
    InvitationActorNotAuthorizedError,
    InvitationAlreadyAcceptedError,
    InvitationAlreadyPendingError,
    InvitationExpiredError,
    InvitationIssuerNotAuthorizedError,
    InvitationMembershipAlreadyExistsError,
    InvitationNotFoundError,
    InvitationPasswordRequiredError,
    InvitationRevokedError,
    InvitationTokenInvalidError,
)
from clinicops.tenancy.exceptions import (
    InvalidTenantNameError as TenancyInvalidTenantNameError,
)
from clinicops.tenancy.exceptions import (
    TenantDisabledError as TenancyTenantDisabledError,
)
from clinicops.tenancy.exceptions import (
    TenantNotFoundError as TenancyTenantNotFoundError,
)

logger = logging.getLogger("clinicops.http")

PROBLEM_MEDIA_TYPE = "application/problem+json"
BEARER_CHALLENGE_HEADER = {"WWW-Authenticate": "Bearer"}

type ErrorLocation = str | int


class ValidationIssue(BaseModel):
    """Sanitized validation failure exposed to API clients."""

    location: list[ErrorLocation]
    message: str
    type: str


class ProblemDetails(BaseModel):
    """RFC 9457-compatible public API problem response."""

    type: str
    title: str
    status: int
    detail: str
    code: str
    request_id: str
    correlation_id: str
    errors: list[ValidationIssue] | None = None


@dataclass(frozen=True, slots=True)
class HttpProblemDefinition:
    """Stable public definition for one HTTP failure class."""

    code: str
    title: str
    detail: str


HTTP_PROBLEM_DEFINITIONS: dict[int, HttpProblemDefinition] = {
    status.HTTP_400_BAD_REQUEST: HttpProblemDefinition(
        code="bad_request",
        title="Bad request",
        detail="The request could not be processed.",
    ),
    status.HTTP_401_UNAUTHORIZED: HttpProblemDefinition(
        code="unauthorized",
        title="Authentication required",
        detail="Authentication is required.",
    ),
    status.HTTP_403_FORBIDDEN: HttpProblemDefinition(
        code="forbidden",
        title="Operation forbidden",
        detail="The requested operation is not allowed.",
    ),
    status.HTTP_404_NOT_FOUND: HttpProblemDefinition(
        code="not_found",
        title="Resource not found",
        detail="The requested resource was not found.",
    ),
    status.HTTP_405_METHOD_NOT_ALLOWED: HttpProblemDefinition(
        code="method_not_allowed",
        title="Method not allowed",
        detail="The HTTP method is not allowed for this resource.",
    ),
    status.HTTP_409_CONFLICT: HttpProblemDefinition(
        code="conflict",
        title="Resource conflict",
        detail="The request conflicts with the current resource state.",
    ),
    status.HTTP_429_TOO_MANY_REQUESTS: HttpProblemDefinition(
        code="rate_limit_exceeded",
        title="Rate limit exceeded",
        detail="Too many requests were received.",
    ),
}

AUTHENTICATION_ERRORS = (
    InvalidCredentialsError,
    AccessTokenInvalidError,
    AccessTokenExpiredError,
    AuthenticationSessionNotFoundError,
    AuthenticationSessionInactiveError,
    AuthenticationSessionExpiredError,
    RefreshTokenInvalidError,
    RefreshTokenExpiredError,
    UserDisabledError,
)

TENANT_NOT_FOUND_ERRORS = (
    TenantNotFoundError,
    TenantMembershipNotFoundError,
    TenancyTenantNotFoundError,
)

TENANT_FORBIDDEN_ERRORS = (
    TenantDisabledError,
    TenantMembershipDisabledError,
    TenantPermissionDeniedError,
    TenancyTenantDisabledError,
)

INVITATION_NOT_FOUND_ERRORS = (InvitationNotFoundError,)

INVITATION_FORBIDDEN_ERRORS = (
    InvitationIssuerNotAuthorizedError,
    InvitationActorNotAuthorizedError,
)

INVITATION_CONFLICT_ERRORS = (
    InvitationAlreadyPendingError,
    InvitationMembershipAlreadyExistsError,
    InvitationAlreadyAcceptedError,
    InvitationRevokedError,
    InvitationExpiredError,
)

ONBOARDING_NOT_FOUND_ERRORS = (UserNotFoundError,)

ONBOARDING_BAD_REQUEST_ERRORS = (
    TenancyInvalidTenantNameError,
    InvitationTokenInvalidError,
    InvitationPasswordRequiredError,
)


def _problem_type(code: str) -> str:
    """Return the stable problem type URI for one public error code."""

    return f"urn:clinicops:problem:{code}"


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


def _problem_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    errors: list[ValidationIssue] | None = None,
    additional_headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build one public Problem Details response."""

    request_id, correlation_id = _trace_identifiers(request)
    payload = ProblemDetails(
        type=_problem_type(code),
        title=title,
        status=status_code,
        detail=detail,
        code=code,
        request_id=request_id,
        correlation_id=correlation_id,
        errors=errors,
    )

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
        headers=_response_headers(
            request_id,
            correlation_id,
            additional_headers,
        ),
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _application_problem(
    exception: ApplicationError,
) -> tuple[int, str, Mapping[str, str] | None]:
    """Map an application failure to status, title, and headers."""

    if isinstance(exception, DatabaseUnavailableError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Service unavailable",
            None,
        )

    if isinstance(exception, AUTHENTICATION_ERRORS):
        return (
            status.HTTP_401_UNAUTHORIZED,
            "Authentication failed",
            BEARER_CHALLENGE_HEADER,
        )

    if isinstance(exception, TENANT_NOT_FOUND_ERRORS):
        return (
            status.HTTP_404_NOT_FOUND,
            "Resource not found",
            None,
        )

    if isinstance(exception, TENANT_FORBIDDEN_ERRORS):
        return (
            status.HTTP_403_FORBIDDEN,
            "Operation forbidden",
            None,
        )

    if isinstance(exception, INVITATION_NOT_FOUND_ERRORS):
        return (
            status.HTTP_404_NOT_FOUND,
            "Resource not found",
            None,
        )

    if isinstance(exception, INVITATION_FORBIDDEN_ERRORS):
        return (
            status.HTTP_403_FORBIDDEN,
            "Operation forbidden",
            None,
        )

    if isinstance(exception, INVITATION_CONFLICT_ERRORS):
        return (
            status.HTTP_409_CONFLICT,
            "Resource conflict",
            None,
        )

    if isinstance(exception, ONBOARDING_NOT_FOUND_ERRORS):
        return (
            status.HTTP_404_NOT_FOUND,
            "Resource not found",
            None,
        )

    if isinstance(exception, ONBOARDING_BAD_REQUEST_ERRORS):
        return (
            status.HTTP_400_BAD_REQUEST,
            "Application request failed",
            None,
        )

    return (
        status.HTTP_400_BAD_REQUEST,
        "Application request failed",
        None,
    )


async def application_error_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Render an expected application failure."""

    assert isinstance(exception, ApplicationError)

    status_code, title, additional_headers = _application_problem(exception)

    return _problem_response(
        request=request,
        status_code=status_code,
        code=exception.code,
        title=title,
        detail=exception.public_message,
        additional_headers=additional_headers,
    )


async def request_validation_error_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Render a sanitized request validation failure."""

    assert isinstance(exception, RequestValidationError)

    errors = [
        ValidationIssue(
            location=list(error["loc"]),
            message=str(error["msg"]),
            type=str(error["type"]),
        )
        for error in exception.errors()
    ]

    return _problem_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="request_validation_error",
        title="Request validation failed",
        detail="The request could not be validated.",
        errors=errors,
    )


async def http_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Render framework and route-level HTTP failures."""

    assert isinstance(exception, StarletteHTTPException)

    definition = HTTP_PROBLEM_DEFINITIONS.get(
        exception.status_code,
        HttpProblemDefinition(
            code="http_error",
            title="HTTP request failed",
            detail="The request could not be completed.",
        ),
    )
    additional_headers = dict(exception.headers or {})

    if exception.status_code == status.HTTP_401_UNAUTHORIZED:
        additional_headers.setdefault(
            "WWW-Authenticate",
            "Bearer",
        )

    return _problem_response(
        request=request,
        status_code=exception.status_code,
        code=definition.code,
        title=definition.title,
        detail=definition.detail,
        additional_headers=additional_headers,
    )


async def unhandled_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Log and sanitize an unexpected application failure."""

    logger.error(
        "unhandled_exception",
        exc_info=(
            type(exception),
            exception,
            exception.__traceback__,
        ),
    )

    return _problem_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_server_error",
        title="Internal server error",
        detail="An unexpected error occurred.",
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
