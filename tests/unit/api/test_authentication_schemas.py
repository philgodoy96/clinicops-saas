from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinicops.api.v1.authentication.schemas import (
    CurrentPrincipalResponse,
    LoginRequest,
    RefreshRequest,
    TokenPairResponse,
)

FIXED_NOW = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)


def test_login_request_normalizes_email_and_preserves_password() -> None:
    password = "  correct horse battery staple  "

    request = LoginRequest(
        email="  User@Example.com  ",
        password=password,
    )

    assert request.email == "User@Example.com"
    assert request.password == password
    assert password not in repr(request)


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "", "password": "valid-password"},
        {"email": "user@example.com", "password": ""},
    ],
)
def test_login_request_rejects_empty_credentials(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        LoginRequest.model_validate(payload)


def test_authentication_requests_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LoginRequest.model_validate(
            {
                "email": "user@example.com",
                "password": "valid-password",
                "tenant_id": str(uuid4()),
            }
        )

    with pytest.raises(ValidationError):
        RefreshRequest.model_validate(
            {
                "refresh_token": "token-id.random-secret",
                "session_id": str(uuid4()),
            }
        )


def test_refresh_request_hides_token_from_representation() -> None:
    plaintext_token = "token-id.random-secret"

    request = RefreshRequest(
        refresh_token=plaintext_token,
    )

    assert request.refresh_token == plaintext_token
    assert plaintext_token not in repr(request)


def test_token_pair_response_serializes_transport_contract() -> None:
    user_id = uuid4()
    session_id = uuid4()
    access_token = "signed-access-token"
    refresh_token = "token-id.random-secret"
    access_token_expires_at = FIXED_NOW + timedelta(minutes=15)
    session_expires_at = FIXED_NOW + timedelta(days=30)

    response = TokenPairResponse(
        user_id=user_id,
        session_id=session_id,
        access_token=access_token,
        access_token_expires_at=access_token_expires_at,
        refresh_token=refresh_token,
        session_expires_at=session_expires_at,
    )

    assert response.model_dump(mode="json") == {
        "user_id": str(user_id),
        "session_id": str(session_id),
        "access_token": access_token,
        "token_type": "bearer",
        "access_token_expires_at": (access_token_expires_at.isoformat().replace("+00:00", "Z")),
        "refresh_token": refresh_token,
        "session_expires_at": (session_expires_at.isoformat().replace("+00:00", "Z")),
    }
    assert access_token not in repr(response)
    assert refresh_token not in repr(response)


def test_token_pair_response_rejects_non_bearer_type() -> None:
    with pytest.raises(ValidationError):
        TokenPairResponse.model_validate(
            {
                "user_id": str(uuid4()),
                "session_id": str(uuid4()),
                "access_token": "signed-access-token",
                "token_type": "basic",
                "access_token_expires_at": FIXED_NOW,
                "refresh_token": "token-id.random-secret",
                "session_expires_at": (FIXED_NOW + timedelta(days=30)),
            }
        )


def test_current_principal_response_contains_only_global_context() -> None:
    user_id = uuid4()
    session_id = uuid4()

    response = CurrentPrincipalResponse(
        user_id=user_id,
        session_id=session_id,
        authenticated_at=FIXED_NOW - timedelta(minutes=5),
        access_token_expires_at=FIXED_NOW + timedelta(minutes=10),
        session_expires_at=FIXED_NOW + timedelta(days=29),
    )
    body = response.model_dump(mode="json")

    assert body["user_id"] == str(user_id)
    assert body["session_id"] == str(session_id)
    assert "tenant_id" not in body
    assert "membership_id" not in body
    assert "role" not in body
    assert "permissions" not in body
