from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import jwt

from clinicops.authentication.config import (
    JWT_ALGORITHM,
    AuthenticationTokenConfig,
)
from clinicops.authentication.exceptions import (
    AccessTokenExpiredError,
    AccessTokenInvalidError,
)

ACCESS_TOKEN_TYPE = "access"
REQUIRED_ACCESS_TOKEN_CLAIMS = [
    "iss",
    "aud",
    "sub",
    "sid",
    "jti",
    "iat",
    "exp",
    "typ",
]


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    """One signed access token and its absolute expiration."""

    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Trusted global identity claims decoded from an access token."""

    user_id: UUID
    session_id: UUID
    token_id: UUID
    issued_at: datetime
    expires_at: datetime


class AccessTokenCodec:
    """Issue and validate short-lived global access JWTs."""

    def __init__(self, config: AuthenticationTokenConfig) -> None:
        self._config = config

    def issue(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        issued_at: datetime,
    ) -> IssuedAccessToken:
        """Issue an access token without tenant authorization claims."""

        normalized_issued_at = _normalize_datetime(issued_at)
        expires_at = normalized_issued_at + self._config.access_token_lifetime
        payload: dict[str, object] = {
            "iss": self._config.issuer,
            "aud": self._config.audience,
            "sub": str(user_id),
            "sid": str(session_id),
            "jti": str(uuid4()),
            "iat": int(normalized_issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "typ": ACCESS_TOKEN_TYPE,
        }
        token = jwt.encode(
            payload,
            self._config.signing_key,
            algorithm=JWT_ALGORITHM,
        )

        return IssuedAccessToken(
            token=token,
            expires_at=expires_at,
        )

    def decode(
        self,
        token: str,
        *,
        now: datetime,
    ) -> AccessTokenClaims:
        """Validate an access token and return trusted global claims."""

        try:
            payload = jwt.decode(
                token,
                self._config.signing_key,
                algorithms=[JWT_ALGORITHM],
                audience=self._config.audience,
                issuer=self._config.issuer,
                options={
                    "require": REQUIRED_ACCESS_TOKEN_CLAIMS,
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
            claims = _parse_access_token_claims(payload)
        except (jwt.InvalidTokenError, TypeError, ValueError) as exc:
            raise AccessTokenInvalidError() from exc

        normalized_now = _normalize_datetime(now)

        if normalized_now >= claims.expires_at:
            raise AccessTokenExpiredError()

        if claims.issued_at > normalized_now:
            raise AccessTokenInvalidError()

        return claims


def _parse_access_token_claims(
    payload: dict[str, Any],
) -> AccessTokenClaims:
    """Parse structurally valid access token claims."""

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise AccessTokenInvalidError()

    subject = payload["sub"]
    session_id = payload["sid"]
    token_id = payload["jti"]
    issued_at = payload["iat"]
    expires_at = payload["exp"]

    if not isinstance(subject, str):
        raise AccessTokenInvalidError()

    if not isinstance(session_id, str):
        raise AccessTokenInvalidError()

    if not isinstance(token_id, str):
        raise AccessTokenInvalidError()

    if not isinstance(issued_at, int):
        raise AccessTokenInvalidError()

    if not isinstance(expires_at, int):
        raise AccessTokenInvalidError()

    if expires_at <= issued_at:
        raise AccessTokenInvalidError()

    return AccessTokenClaims(
        user_id=UUID(subject),
        session_id=UUID(session_id),
        token_id=UUID(token_id),
        issued_at=datetime.fromtimestamp(issued_at, tz=UTC),
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
    )


def _normalize_datetime(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime with second precision."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required.")

    return value.astimezone(UTC).replace(microsecond=0)
