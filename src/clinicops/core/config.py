from enum import StrEnum
from functools import lru_cache
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LOCAL_AUTH_SIGNING_KEY = "local-development-signing-key-change-me"
MINIMUM_AUTH_SIGNING_KEY_BYTES = 32


class Environment(StrEnum):
    """Supported application runtime environments."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


type LogLevel = Literal[
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]

NonEmptySetting = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
    ),
]


class Settings(BaseSettings):
    """Validated application configuration loaded from the environment."""

    app_name: str = "ClinicOps SaaS"
    app_version: str = "0.1.0"
    environment: Environment = Environment.LOCAL
    log_level: LogLevel = "INFO"
    database_url: str = "postgresql+psycopg://clinicops:clinicops@localhost:5432/clinicops"
    database_connect_timeout_seconds: int = Field(
        default=3,
        ge=1,
        le=30,
    )
    auth_issuer: NonEmptySetting = "clinicops"
    auth_audience: NonEmptySetting = "clinicops-api"
    auth_signing_key: SecretStr = SecretStr(DEFAULT_LOCAL_AUTH_SIGNING_KEY)

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CLINICOPS_",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_authentication_configuration(self) -> Self:
        """Reject weak or deployment-unsafe authentication secrets."""

        signing_key = self.auth_signing_key.get_secret_value()

        if len(signing_key.encode("utf-8")) < MINIMUM_AUTH_SIGNING_KEY_BYTES:
            raise ValueError(
                "Authentication signing key must contain "
                f"at least {MINIMUM_AUTH_SIGNING_KEY_BYTES} UTF-8 bytes."
            )

        deployed_environments = {
            Environment.STAGING,
            Environment.PRODUCTION,
        }

        if (
            self.environment in deployed_environments
            and signing_key == DEFAULT_LOCAL_AUTH_SIGNING_KEY
        ):
            raise ValueError(
                "The default local authentication signing key "
                "cannot be used in a deployed environment."
            )

        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
