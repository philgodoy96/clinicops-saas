from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from clinicops.api.v1.authentication.routes import (
    login,
    refresh_authentication,
)
from clinicops.api.v1.authentication.schemas import (
    LoginRequest,
    RefreshRequest,
)
from clinicops.authentication.exceptions import RefreshTokenInvalidError
from clinicops.authentication.services.authenticate_user import (
    AuthenticatedSession,
    AuthenticateUserCommand,
    AuthenticateUserService,
)
from clinicops.authentication.services.refresh_authentication import (
    AuthenticationCompromiseReason,
    CompromisedAuthenticationSession,
    RefreshAuthenticationCommand,
    RefreshAuthenticationService,
    RefreshedAuthentication,
)
from clinicops.core.config import Environment
from clinicops.main import create_app
from tests.conftest import IsolatedSettings

FIXED_NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)
SIGNING_KEY = "test-signing-key-with-at-least-32-bytes"


class RecordingSession:
    """Track route-owned transaction commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeAuthenticateUserService:
    """Return one deterministic login result."""

    def __init__(self, result: AuthenticatedSession) -> None:
        self.result = result
        self.received_session: Session | None = None
        self.received_command: AuthenticateUserCommand | None = None

    def execute(
        self,
        session: Session,
        command: AuthenticateUserCommand,
    ) -> AuthenticatedSession:
        self.received_session = session
        self.received_command = command
        return self.result


class FakeRefreshAuthenticationService:
    """Return one deterministic refresh result or failure."""

    def __init__(
        self,
        result: (RefreshedAuthentication | CompromisedAuthenticationSession | Exception),
    ) -> None:
        self.result = result
        self.received_session: Session | None = None
        self.received_command: RefreshAuthenticationCommand | None = None

    def execute(
        self,
        session: Session,
        command: RefreshAuthenticationCommand,
    ) -> RefreshedAuthentication | CompromisedAuthenticationSession:
        self.received_session = session
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


def build_authenticated_session() -> AuthenticatedSession:
    """Build one successful login result."""

    return AuthenticatedSession(
        user_id=uuid4(),
        session_id=uuid4(),
        access_token="signed-access-token",
        access_token_expires_at=FIXED_NOW + timedelta(minutes=15),
        refresh_token="token-id.random-secret",
        session_expires_at=FIXED_NOW + timedelta(days=30),
    )


def build_refreshed_authentication() -> RefreshedAuthentication:
    """Build one successful refresh result."""

    return RefreshedAuthentication(
        user_id=uuid4(),
        session_id=uuid4(),
        access_token="replacement-access-token",
        access_token_expires_at=FIXED_NOW + timedelta(minutes=15),
        refresh_token="replacement-id.random-secret",
        session_expires_at=FIXED_NOW + timedelta(days=29),
    )


def test_login_commits_before_returning_token_pair() -> None:
    result = build_authenticated_session()
    recording_session = RecordingSession()
    fake_service = FakeAuthenticateUserService(result)
    payload = LoginRequest(
        email="  User@Example.com  ",
        password="password with spaces ",
    )

    response = login(
        payload=payload,
        session=cast(Session, recording_session),
        service=cast(AuthenticateUserService, fake_service),
    )

    assert recording_session.commit_count == 1
    assert fake_service.received_session is cast(Session, recording_session)
    assert fake_service.received_command == AuthenticateUserCommand(
        email="User@Example.com",
        password="password with spaces ",
    )
    assert response.user_id == result.user_id
    assert response.session_id == result.session_id
    assert response.access_token == result.access_token
    assert response.refresh_token == result.refresh_token
    assert response.token_type == "bearer"


def test_refresh_commits_rotation_before_returning_tokens() -> None:
    result = build_refreshed_authentication()
    recording_session = RecordingSession()
    fake_service = FakeRefreshAuthenticationService(result)
    plaintext_token = "current-id.random-secret"

    response = refresh_authentication(
        payload=RefreshRequest(refresh_token=plaintext_token),
        session=cast(Session, recording_session),
        service=cast(RefreshAuthenticationService, fake_service),
    )

    assert recording_session.commit_count == 1
    assert fake_service.received_session is cast(Session, recording_session)
    assert fake_service.received_command == RefreshAuthenticationCommand(
        refresh_token=plaintext_token,
    )
    assert response.access_token == result.access_token
    assert response.refresh_token == result.refresh_token
    assert response.session_id == result.session_id


def test_refresh_reuse_commits_compromise_before_failure() -> None:
    compromised_result = CompromisedAuthenticationSession(
        session_id=uuid4(),
        compromised_at=FIXED_NOW,
        reason=AuthenticationCompromiseReason.REFRESH_TOKEN_REUSE,
    )
    recording_session = RecordingSession()
    fake_service = FakeRefreshAuthenticationService(compromised_result)

    with pytest.raises(RefreshTokenInvalidError):
        refresh_authentication(
            payload=RefreshRequest(refresh_token="consumed-id.random-secret"),
            session=cast(Session, recording_session),
            service=cast(
                RefreshAuthenticationService,
                fake_service,
            ),
        )

    assert recording_session.commit_count == 1


def test_refresh_failure_does_not_commit() -> None:
    recording_session = RecordingSession()
    fake_service = FakeRefreshAuthenticationService(RefreshTokenInvalidError())

    with pytest.raises(RefreshTokenInvalidError):
        refresh_authentication(
            payload=RefreshRequest(refresh_token="invalid-id.random-secret"),
            session=cast(Session, recording_session),
            service=cast(
                RefreshAuthenticationService,
                fake_service,
            ),
        )

    assert recording_session.commit_count == 0


def test_openapi_exposes_login_and_refresh_routes() -> None:
    application = create_app(
        IsolatedSettings(
            environment=Environment.TEST,
            auth_signing_key=SecretStr(SIGNING_KEY),
        )
    )
    paths = application.openapi()["paths"]

    assert "post" in paths["/api/v1/auth/login"]
    assert "post" in paths["/api/v1/auth/refresh"]
    assert paths["/api/v1/auth/login"]["post"]["summary"] == ("Create an authentication session")
    assert paths["/api/v1/auth/refresh"]["post"]["summary"] == ("Rotate authentication credentials")
