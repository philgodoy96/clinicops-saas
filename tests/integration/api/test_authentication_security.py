from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clinicops.api.errors import PROBLEM_MEDIA_TYPE
from clinicops.authentication.models import (
    AuthSession,
    AuthSessionStatus,
    RefreshToken,
    RefreshTokenStatus,
)
from clinicops.authentication.refresh_tokens import parse_refresh_token
from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import Argon2PasswordHasher

PASSWORD = "Correct-Horse-Battery-Staple-42!"


@dataclass(frozen=True, slots=True)
class CommittedPasswordUser:
    """Committed global user available to independent HTTP sessions."""

    user_id: UUID
    email: str
    password: str
    status: UserStatus


class PasswordUserFactory(Protocol):
    """Create committed password users tracked for test cleanup."""

    def __call__(
        self,
        *,
        password: str = PASSWORD,
        status: UserStatus = UserStatus.ACTIVE,
    ) -> CommittedPasswordUser:
        """Persist and return one isolated password user."""
        ...


@pytest.fixture
def committed_password_user_factory() -> Iterator[PasswordUserFactory]:
    """Create committed users and remove their authentication state."""

    created_user_ids: list[UUID] = []

    def factory(
        *,
        password: str = PASSWORD,
        status: UserStatus = UserStatus.ACTIVE,
    ) -> CommittedPasswordUser:
        email = f"auth-api-{uuid4()}@example.com"
        disabled_at = datetime.now(UTC) if status is UserStatus.DISABLED else None
        user = User(
            email=email,
            status=status,
            disabled_at=disabled_at,
            password_credential=PasswordCredential(
                password_hash=Argon2PasswordHasher().hash(password),
            ),
        )

        with Session(get_engine()) as session:
            session.add(user)
            session.commit()
            user_id = user.id

        created_user_ids.append(user_id)

        return CommittedPasswordUser(
            user_id=user_id,
            email=email,
            password=password,
            status=status,
        )

    yield factory

    if not created_user_ids:
        return

    with Session(get_engine()) as session:
        authentication_sessions = session.scalars(
            select(AuthSession).where(AuthSession.user_id.in_(created_user_ids))
        ).all()

        for authentication_session in authentication_sessions:
            session.delete(authentication_session)

        session.flush()

        users = session.scalars(select(User).where(User.id.in_(created_user_ids))).all()

        for user in users:
            session.delete(user)

        session.commit()


def authorization_headers(access_token: str) -> dict[str, str]:
    """Build the bearer authorization header for one access token."""

    return {"Authorization": f"Bearer {access_token}"}


def assert_bearer_unauthorized(
    response: Response,
    *,
    expected_code: str,
) -> dict[str, object]:
    """Assert the standard protected-request failure contract."""

    body = cast(dict[str, object], response.json())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["type"] == (f"urn:clinicops:problem:{expected_code}")
    assert body["status"] == 401
    assert body["code"] == expected_code

    return body


def count_authentication_sessions(user_id: UUID) -> int:
    """Count committed authentication sessions for one user."""

    with Session(get_engine()) as session:
        count = session.scalar(
            select(func.count(AuthSession.id)).where(AuthSession.user_id == user_id)
        )

    return int(count or 0)


def test_login_me_and_logout_enforce_current_session_state(
    client: TestClient,
    committed_password_user_factory: PasswordUserFactory,
) -> None:
    user = committed_password_user_factory()

    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": user.password,
        },
    )

    assert login_response.status_code == 200
    login_body = login_response.json()
    access_token = login_body["access_token"]
    refresh_token = login_body["refresh_token"]
    session_id = UUID(login_body["session_id"])

    assert login_body["user_id"] == str(user.user_id)
    assert login_body["token_type"] == "bearer"

    current_user_response = client.get(
        "/api/v1/auth/me",
        headers=authorization_headers(access_token),
    )

    assert current_user_response.status_code == 200
    current_user_body = current_user_response.json()
    assert current_user_body["user_id"] == str(user.user_id)
    assert current_user_body["session_id"] == str(session_id)
    assert "access_token_id" not in current_user_body
    assert "tenant_id" not in current_user_body
    assert "role" not in current_user_body
    assert "permissions" not in current_user_body

    logout_response = client.post(
        "/api/v1/auth/logout",
        headers=authorization_headers(access_token),
    )

    assert logout_response.status_code == 204
    assert logout_response.content == b""

    rejected_me_response = client.get(
        "/api/v1/auth/me",
        headers=authorization_headers(access_token),
    )
    assert_bearer_unauthorized(
        rejected_me_response,
        expected_code="authentication_session_inactive",
    )

    rejected_refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert_bearer_unauthorized(
        rejected_refresh_response,
        expected_code="authentication_session_inactive",
    )

    with Session(get_engine()) as session:
        authentication_session = session.get(
            AuthSession,
            session_id,
        )
        tokens = session.scalars(
            select(RefreshToken).where(RefreshToken.session_id == session_id)
        ).all()

        assert authentication_session is not None
        assert authentication_session.status is AuthSessionStatus.REVOKED
        assert len(tokens) == 1
        assert tokens[0].status is RefreshTokenStatus.REVOKED


def test_refresh_reuse_compromises_session_and_rejects_replacement(
    client: TestClient,
    committed_password_user_factory: PasswordUserFactory,
) -> None:
    user = committed_password_user_factory()

    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": user.password,
        },
    )

    assert login_response.status_code == 200
    login_body = login_response.json()
    original_refresh_token = login_body["refresh_token"]
    session_id = UUID(login_body["session_id"])

    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original_refresh_token},
    )

    assert refresh_response.status_code == 200
    refresh_body = refresh_response.json()
    replacement_access_token = refresh_body["access_token"]
    replacement_refresh_token = refresh_body["refresh_token"]

    original_token_id = parse_refresh_token(original_refresh_token).token_id
    replacement_token_id = parse_refresh_token(replacement_refresh_token).token_id

    with Session(get_engine()) as session:
        authentication_session = session.get(
            AuthSession,
            session_id,
        )
        original_token = session.get(
            RefreshToken,
            original_token_id,
        )
        replacement_token = session.get(
            RefreshToken,
            replacement_token_id,
        )

        assert authentication_session is not None
        assert authentication_session.status is AuthSessionStatus.ACTIVE
        assert original_token is not None
        assert original_token.status is RefreshTokenStatus.CONSUMED
        assert replacement_token is not None
        assert replacement_token.status is RefreshTokenStatus.ACTIVE

    reuse_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original_refresh_token},
    )
    assert_bearer_unauthorized(
        reuse_response,
        expected_code="refresh_token_invalid",
    )

    with Session(get_engine()) as session:
        authentication_session = session.get(
            AuthSession,
            session_id,
        )
        replacement_token = session.get(
            RefreshToken,
            replacement_token_id,
        )

        assert authentication_session is not None
        assert authentication_session.status is AuthSessionStatus.COMPROMISED
        assert replacement_token is not None
        assert replacement_token.status is RefreshTokenStatus.REVOKED

    rejected_replacement_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": replacement_refresh_token},
    )
    assert_bearer_unauthorized(
        rejected_replacement_response,
        expected_code="authentication_session_inactive",
    )

    rejected_access_response = client.get(
        "/api/v1/auth/me",
        headers=authorization_headers(replacement_access_token),
    )
    assert_bearer_unauthorized(
        rejected_access_response,
        expected_code="authentication_session_inactive",
    )


def test_invalid_login_attempts_share_generic_failure_contract(
    client: TestClient,
    committed_password_user_factory: PasswordUserFactory,
) -> None:
    active_user = committed_password_user_factory()
    disabled_user = committed_password_user_factory(
        status=UserStatus.DISABLED,
    )

    wrong_password_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": active_user.email,
            "password": "incorrect-password",
        },
    )
    unknown_user_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": f"missing-{uuid4()}@example.com",
            "password": PASSWORD,
        },
    )
    disabled_user_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": disabled_user.email,
            "password": disabled_user.password,
        },
    )

    responses = [
        wrong_password_response,
        unknown_user_response,
        disabled_user_response,
    ]
    signatures: list[dict[str, object]] = []

    for response in responses:
        body = assert_bearer_unauthorized(
            response,
            expected_code="invalid_credentials",
        )
        signatures.append(
            {
                key: body[key]
                for key in (
                    "type",
                    "title",
                    "status",
                    "detail",
                    "code",
                )
            }
        )
        assert active_user.password not in response.text
        assert disabled_user.password not in response.text

    assert signatures[0] == signatures[1] == signatures[2]
    assert count_authentication_sessions(active_user.user_id) == 0
    assert count_authentication_sessions(disabled_user.user_id) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {
            "email": "user@example.com",
            "password": "",
        },
        {
            "email": "user@example.com",
            "password": PASSWORD,
            "tenant_id": str(uuid4()),
        },
    ],
)
def test_invalid_login_payload_does_not_create_session(
    client: TestClient,
    committed_password_user_factory: PasswordUserFactory,
    payload: dict[str, str],
) -> None:
    user = committed_password_user_factory()
    request_payload = {
        **payload,
        "email": user.email,
    }

    response = client.post(
        "/api/v1/auth/login",
        json=request_payload,
    )
    body = response.json()

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["type"] == ("urn:clinicops:problem:request_validation_error")
    assert body["status"] == 422
    assert body["code"] == "request_validation_error"
    assert count_authentication_sessions(user.user_id) == 0
