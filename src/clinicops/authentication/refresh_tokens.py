import re
from dataclasses import dataclass, field
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from uuid import UUID, uuid4

from clinicops.authentication.exceptions import (
    RefreshTokenInvalidError,
)

REFRESH_TOKEN_SECRET_BYTES = 32
REFRESH_TOKEN_SECRET_LENGTH = 43
_REFRESH_TOKEN_SECRET_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{{REFRESH_TOKEN_SECRET_LENGTH}}}")


@dataclass(frozen=True, slots=True)
class GeneratedRefreshToken:
    """One plaintext refresh token and its persistence-safe digest."""

    token_id: UUID
    plaintext: str = field(repr=False)
    digest: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ParsedRefreshToken:
    """Selector and digest derived from a supplied refresh token."""

    token_id: UUID
    digest: str = field(repr=False)


def generate_refresh_token(
    token_id: UUID | None = None,
) -> GeneratedRefreshToken:
    """Generate a UUID-selected, high-entropy refresh token."""

    resolved_token_id = token_id if token_id is not None else uuid4()
    secret = token_urlsafe(REFRESH_TOKEN_SECRET_BYTES)
    plaintext = f"{resolved_token_id}.{secret}"

    return GeneratedRefreshToken(
        token_id=resolved_token_id,
        plaintext=plaintext,
        digest=digest_refresh_token(plaintext),
    )


def parse_refresh_token(plaintext_token: str) -> ParsedRefreshToken:
    """Validate a refresh token and return its selector and digest."""

    if plaintext_token.count(".") != 1:
        raise RefreshTokenInvalidError()

    selector, secret = plaintext_token.split(".", maxsplit=1)

    try:
        token_id = UUID(selector)
    except ValueError as exc:
        raise RefreshTokenInvalidError() from exc

    if str(token_id) != selector:
        raise RefreshTokenInvalidError()

    if _REFRESH_TOKEN_SECRET_PATTERN.fullmatch(secret) is None:
        raise RefreshTokenInvalidError()

    return ParsedRefreshToken(
        token_id=token_id,
        digest=digest_refresh_token(plaintext_token),
    )


def digest_refresh_token(plaintext_token: str) -> str:
    """Return the SHA-256 digest persisted for a refresh token."""

    return sha256(plaintext_token.encode("utf-8")).hexdigest()


def refresh_token_matches(
    plaintext_token: str,
    expected_digest: str,
) -> bool:
    """Compare a supplied refresh token against a stored digest."""

    supplied_digest = digest_refresh_token(plaintext_token)

    return compare_digest(supplied_digest, expected_digest)
