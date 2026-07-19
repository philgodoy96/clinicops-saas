from dataclasses import dataclass, field
from datetime import timedelta

from clinicops.authentication.exceptions import (
    AuthenticationConfigurationError,
)

ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)
AUTHENTICATION_SESSION_LIFETIME = timedelta(days=30)
JWT_ALGORITHM = "HS256"
MINIMUM_SIGNING_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class AuthenticationTokenConfig:
    """Validated configuration for access token issuance and decoding."""

    issuer: str
    audience: str
    signing_key: str = field(repr=False)
    access_token_lifetime: timedelta = ACCESS_TOKEN_LIFETIME

    def __post_init__(self) -> None:
        """Reject unsafe or incomplete token configuration."""

        if not self.issuer.strip():
            raise AuthenticationConfigurationError()

        if not self.audience.strip():
            raise AuthenticationConfigurationError()

        if len(self.signing_key.encode("utf-8")) < MINIMUM_SIGNING_KEY_BYTES:
            raise AuthenticationConfigurationError()

        if self.access_token_lifetime <= timedelta(0):
            raise AuthenticationConfigurationError()
