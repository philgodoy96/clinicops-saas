from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from clinicops.api.dependencies import get_database_session
from clinicops.api.errors import PROBLEM_MEDIA_TYPE, register_exception_handlers
from clinicops.api.middleware.request_context import RequestContextMiddleware
from clinicops.api.v1.authentication.dependencies import (
    AuthenticatedPrincipalDependency,
    get_resolve_authenticated_principal_service,
)
from clinicops.authentication.exceptions import (
    AccessTokenExpiredError,
    AuthenticationSessionInactiveError,
    AuthenticationSessionNotFoundError,
)
from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
    ResolveAuthenticatedPrincipalCommand,
    ResolveAuthenticatedPrincipalService,
)
from clinicops.identity.exceptions import UserDisabledError

FIXED_NOW = datetime(2026, 8, 3, 15, 0, tzinfo=UTC)


class RecordingSession:
    """Track accidental write commits during principal resolution."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeResolveAuthenticatedPrincipalService:
    """Return one deterministic principal or authentication failure."""

    def __init__(
        self,
        result: AuthenticatedPrincipal | Exception,
    ) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: ResolveAuthenticatedPrincipalCommand | None = None

    def execute(
        self,
        session: Session,
        command: ResolveAuthenticatedPrincipalCommand,
    ) -> AuthenticatedPrincipal:
        self.call_count += 1
        self.received_session = session
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


def build_principal() -> AuthenticatedPrincipal:
    """Build one trusted global principal."""

    return AuthenticatedPrincipal(
        user_id=uuid4(),
        session_id=uuid4(),
        access_token_id=uuid4(),
        authenticated_at=FIXED_NOW - timedelta(minutes=5),
        access_token_expires_at=FIXED_NOW + timedelta(minutes=10),
        session_expires_at=FIXED_NOW + timedelta(days=29),
    )


def build_protected_test_app(
    result: AuthenticatedPrincipal | Exception,
) -> tuple[
    FastAPI,
    FakeResolveAuthenticatedPrincipalService,
    RecordingSession,
]:
    """Build an isolated protected API with overridden dependencies."""

    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)

    fake_service = FakeResolveAuthenticatedPrincipalService(result)
    recording_session = RecordingSession()

    def override_principal_service() -> ResolveAuthenticatedPrincipalService:
        return cast(
            ResolveAuthenticatedPrincipalService,
            fake_service,
        )

    def override_database_session() -> Session:
        return cast(Session, recording_session)

    application.dependency_overrides[get_resolve_authenticated_principal_service] = (
        override_principal_service
    )
    application.dependency_overrides[get_database_session] = override_database_session

    @application.get("/protected")
    def read_protected_resource(
        principal: AuthenticatedPrincipalDependency,
    ) -> dict[str, str]:
        return {
            "user_id": str(principal.user_id),
            "session_id": str(principal.session_id),
        }

    return application, fake_service, recording_session


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic credentials"},
        {"Authorization": "Bearer"},
    ],
)
def test_missing_or_malformed_bearer_credentials_return_unauthorized(
    headers: dict[str, str],
) -> None:
    application, fake_service, recording_session = build_protected_test_app(build_principal())

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            "/protected",
            headers=headers,
        )

    body = response.json()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["type"] == "urn:clinicops:problem:unauthorized"
    assert body["status"] == 401
    assert body["code"] == "unauthorized"
    assert body["detail"] == "Authentication is required."
    assert fake_service.call_count == 0
    assert recording_session.commit_count == 0


def test_bearer_token_resolves_trusted_principal_without_commit() -> None:
    principal = build_principal()
    plaintext_token = "signed-access-token"
    application, fake_service, recording_session = build_protected_test_app(principal)

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            "/protected",
            headers={
                "Authorization": f"Bearer {plaintext_token}",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(principal.user_id),
        "session_id": str(principal.session_id),
    }
    assert fake_service.call_count == 1
    assert isinstance(fake_service.received_session, RecordingSession)
    assert fake_service.received_session is recording_session
    assert fake_service.received_command == (
        ResolveAuthenticatedPrincipalCommand(
            access_token=plaintext_token,
        )
    )
    assert recording_session.commit_count == 0


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (
            AccessTokenExpiredError(),
            "access_token_expired",
        ),
        (
            AuthenticationSessionNotFoundError(),
            "authentication_session_not_found",
        ),
        (
            AuthenticationSessionInactiveError(),
            "authentication_session_inactive",
        ),
        (
            UserDisabledError(),
            "user_disabled",
        ),
    ],
)
def test_principal_resolution_failures_return_bearer_unauthorized(
    exception: Exception,
    expected_code: str,
) -> None:
    application, fake_service, recording_session = build_protected_test_app(exception)

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            "/protected",
            headers={
                "Authorization": "Bearer signed-access-token",
            },
        )

    body = response.json()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["type"] == (f"urn:clinicops:problem:{expected_code}")
    assert body["status"] == 401
    assert body["code"] == expected_code
    assert fake_service.call_count == 1
    assert recording_session.commit_count == 0


def test_protected_dependency_registers_bearer_security_scheme() -> None:
    application, _, _ = build_protected_test_app(build_principal())
    openapi_schema = application.openapi()

    security_schemes = openapi_schema["components"]["securitySchemes"]
    protected_operation = openapi_schema["paths"]["/protected"]["get"]

    assert security_schemes["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert protected_operation["security"] == [{"HTTPBearer": []}]
