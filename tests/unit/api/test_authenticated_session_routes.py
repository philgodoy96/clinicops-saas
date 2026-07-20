from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from clinicops.api.v1.authentication.routes import (
    get_current_principal,
    logout,
)
from clinicops.authentication.exceptions import (
    AuthenticationSessionInactiveError,
)
from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
)
from clinicops.authentication.services.revoke_session import (
    RevokeAuthenticationSessionCommand,
    RevokeAuthenticationSessionService,
    RevokedAuthenticationSession,
)
from clinicops.core.config import Environment
from clinicops.main import create_app
from tests.conftest import IsolatedSettings

FIXED_NOW = datetime(2026, 8, 4, 15, 0, tzinfo=UTC)
SIGNING_KEY = "test-signing-key-with-at-least-32-bytes"


class RecordingSession:
    """Track route-owned transaction commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeRevokeAuthenticationSessionService:
    """Return one deterministic revocation result or failure."""

    def __init__(
        self,
        result: RevokedAuthenticationSession | Exception,
    ) -> None:
        self.result = result
        self.received_session: Session | None = None
        self.received_command: RevokeAuthenticationSessionCommand | None = None

    def execute(
        self,
        session: Session,
        command: RevokeAuthenticationSessionCommand,
    ) -> RevokedAuthenticationSession:
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


def test_current_user_returns_only_public_global_context() -> None:
    principal = build_principal()

    response = get_current_principal(principal)
    body = response.model_dump(mode="json")

    assert response.user_id == principal.user_id
    assert response.session_id == principal.session_id
    assert response.authenticated_at == principal.authenticated_at
    assert response.access_token_expires_at == principal.access_token_expires_at
    assert response.session_expires_at == principal.session_expires_at
    assert "access_token_id" not in body
    assert "tenant_id" not in body
    assert "membership_id" not in body
    assert "role" not in body
    assert "permissions" not in body


def test_logout_revokes_current_session_and_commits() -> None:
    principal = build_principal()
    recording_session = RecordingSession()
    revoked_result = RevokedAuthenticationSession(
        session_id=principal.session_id,
        revoked_at=FIXED_NOW,
    )
    fake_service = FakeRevokeAuthenticationSessionService(revoked_result)

    response = logout(
        principal=principal,
        session=cast(Session, recording_session),
        service=cast(
            RevokeAuthenticationSessionService,
            fake_service,
        ),
    )

    assert isinstance(fake_service.received_session, RecordingSession)
    assert fake_service.received_session is recording_session
    assert fake_service.received_command == (
        RevokeAuthenticationSessionCommand(
            user_id=principal.user_id,
            session_id=principal.session_id,
        )
    )
    assert recording_session.commit_count == 1
    assert response.status_code == 204
    assert response.body == b""


def test_logout_failure_does_not_commit() -> None:
    principal = build_principal()
    recording_session = RecordingSession()
    fake_service = FakeRevokeAuthenticationSessionService(AuthenticationSessionInactiveError())

    with pytest.raises(AuthenticationSessionInactiveError):
        logout(
            principal=principal,
            session=cast(Session, recording_session),
            service=cast(
                RevokeAuthenticationSessionService,
                fake_service,
            ),
        )

    assert recording_session.commit_count == 0


def test_openapi_exposes_protected_current_user_and_logout() -> None:
    application = create_app(
        IsolatedSettings(
            environment=Environment.TEST,
            auth_signing_key=SecretStr(SIGNING_KEY),
        )
    )
    openapi_schema = application.openapi()
    paths = openapi_schema["paths"]

    current_user_operation = paths["/api/v1/auth/me"]["get"]
    logout_operation = paths["/api/v1/auth/logout"]["post"]

    assert current_user_operation["summary"] == ("Get the current authenticated principal")
    assert logout_operation["summary"] == ("Revoke the current authentication session")
    assert current_user_operation["security"] == [{"HTTPBearer": []}]
    assert logout_operation["security"] == [{"HTTPBearer": []}]
    assert "204" in logout_operation["responses"]
    assert "content" not in logout_operation["responses"]["204"]
