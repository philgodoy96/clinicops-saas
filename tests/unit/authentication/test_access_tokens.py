from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from clinicops.authentication.access_tokens import (
    ACCESS_TOKEN_TYPE,
    AccessTokenCodec,
)
from clinicops.authentication.config import (
    ACCESS_TOKEN_LIFETIME,
    JWT_ALGORITHM,
    AuthenticationTokenConfig,
)
from clinicops.authentication.exceptions import (
    AccessTokenExpiredError,
    AccessTokenInvalidError,
    AuthenticationConfigurationError,
)

FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
SIGNING_KEY = "development-signing-key-with-32-bytes-minimum"
ISSUER = "clinicops"
AUDIENCE = "clinicops-api"


def build_config(
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    signing_key: str = SIGNING_KEY,
    access_token_lifetime: timedelta = ACCESS_TOKEN_LIFETIME,
) -> AuthenticationTokenConfig:
    """Build deterministic access token configuration."""

    return AuthenticationTokenConfig(
        issuer=issuer,
        audience=audience,
        signing_key=signing_key,
        access_token_lifetime=access_token_lifetime,
    )


def test_access_token_round_trip_returns_global_claims() -> None:
    user_id = uuid4()
    session_id = uuid4()
    codec = AccessTokenCodec(build_config())

    issued = codec.issue(
        user_id=user_id,
        session_id=session_id,
        issued_at=FIXED_NOW,
    )
    claims = codec.decode(issued.token, now=FIXED_NOW)

    assert claims.user_id == user_id
    assert claims.session_id == session_id
    assert claims.issued_at == FIXED_NOW
    assert claims.expires_at == FIXED_NOW + ACCESS_TOKEN_LIFETIME
    assert issued.expires_at == claims.expires_at


def test_access_token_contains_no_tenant_authorization_claims() -> None:
    codec = AccessTokenCodec(build_config())
    issued = codec.issue(
        user_id=uuid4(),
        session_id=uuid4(),
        issued_at=FIXED_NOW,
    )

    payload = jwt.decode(
        issued.token,
        options={"verify_signature": False},
    )

    assert set(payload) == {
        "iss",
        "aud",
        "sub",
        "sid",
        "jti",
        "iat",
        "exp",
        "typ",
    }
    assert payload["typ"] == ACCESS_TOKEN_TYPE
    assert "tenant_id" not in payload
    assert "membership_id" not in payload
    assert "role" not in payload
    assert "permissions" not in payload


def test_access_token_repr_hides_plaintext_token() -> None:
    issued = AccessTokenCodec(build_config()).issue(
        user_id=uuid4(),
        session_id=uuid4(),
        issued_at=FIXED_NOW,
    )

    assert issued.token not in repr(issued)
    assert SIGNING_KEY not in repr(build_config())


def test_access_token_is_expired_at_exact_expiration() -> None:
    codec = AccessTokenCodec(build_config())
    issued = codec.issue(
        user_id=uuid4(),
        session_id=uuid4(),
        issued_at=FIXED_NOW,
    )

    with pytest.raises(AccessTokenExpiredError):
        codec.decode(
            issued.token,
            now=FIXED_NOW + ACCESS_TOKEN_LIFETIME,
        )


def test_access_token_rejects_tampered_signature() -> None:
    codec = AccessTokenCodec(build_config())
    issued = codec.issue(
        user_id=uuid4(),
        session_id=uuid4(),
        issued_at=FIXED_NOW,
    )
    header, payload, signature = issued.token.split(".")
    replacement_character = "A" if signature[0] != "A" else "B"
    tampered_signature = replacement_character + signature[1:]
    tampered_token = f"{header}.{payload}.{tampered_signature}"

    with pytest.raises(AccessTokenInvalidError):
        codec.decode(tampered_token, now=FIXED_NOW)


@pytest.mark.parametrize(
    "decoder_config",
    [
        build_config(issuer="another-issuer"),
        build_config(audience="another-audience"),
        build_config(signing_key="another-signing-key-with-32-bytes-minimum"),
    ],
)
def test_access_token_rejects_untrusted_configuration(
    decoder_config: AuthenticationTokenConfig,
) -> None:
    issued = AccessTokenCodec(build_config()).issue(
        user_id=uuid4(),
        session_id=uuid4(),
        issued_at=FIXED_NOW,
    )

    with pytest.raises(AccessTokenInvalidError):
        AccessTokenCodec(decoder_config).decode(
            issued.token,
            now=FIXED_NOW,
        )


def test_access_token_rejects_wrong_token_type() -> None:
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "jti": str(uuid4()),
        "iat": int(FIXED_NOW.timestamp()),
        "exp": int((FIXED_NOW + ACCESS_TOKEN_LIFETIME).timestamp()),
        "typ": "refresh",
    }
    token = jwt.encode(
        payload,
        SIGNING_KEY,
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(AccessTokenInvalidError):
        AccessTokenCodec(build_config()).decode(
            token,
            now=FIXED_NOW,
        )


def test_access_token_rejects_future_issued_at() -> None:
    codec = AccessTokenCodec(build_config())
    issued = codec.issue(
        user_id=uuid4(),
        session_id=uuid4(),
        issued_at=FIXED_NOW + timedelta(seconds=1),
    )

    with pytest.raises(AccessTokenInvalidError):
        codec.decode(issued.token, now=FIXED_NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": ""},
        {"audience": "   "},
        {"signing_key": "too-short"},
        {"access_token_lifetime": timedelta(0)},
        {"access_token_lifetime": timedelta(seconds=-1)},
    ],
)
def test_authentication_token_config_rejects_unsafe_values(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "signing_key": SIGNING_KEY,
        "access_token_lifetime": ACCESS_TOKEN_LIFETIME,
    }
    values.update(overrides)

    with pytest.raises(AuthenticationConfigurationError):
        AuthenticationTokenConfig(**values)  # type: ignore[arg-type]
