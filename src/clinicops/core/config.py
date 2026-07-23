from enum import StrEnum
from functools import lru_cache
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    BeforeValidator,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LOCAL_AUTH_SIGNING_KEY = "local-development-signing-key-change-me"
DEFAULT_LOCAL_BILLING_WEBHOOK_SECRET = "local-billing-webhook-secret-change-me"
MINIMUM_AUTH_SIGNING_KEY_BYTES = 32
MINIMUM_BILLING_WEBHOOK_SECRET_BYTES = 32
MAXIMUM_WORKER_ID_LENGTH = 255


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

NormalizedWorkerId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAXIMUM_WORKER_ID_LENGTH,
    ),
]


def _reject_bool_as_number(value: object) -> object:
    """Reject booleans before numeric coercion accepts them as 0/1."""

    if isinstance(value, bool):
        raise ValueError("Boolean values are not accepted.")

    return value


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
    billing_webhook_secret: SecretStr = SecretStr(DEFAULT_LOCAL_BILLING_WEBHOOK_SECRET)
    billing_webhook_signature_tolerance_seconds: int = Field(
        default=300,
        ge=0,
        le=3600,
    )
    billing_webhook_max_payload_bytes: int = Field(
        default=256 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    worker_id: NormalizedWorkerId | None = None
    worker_poll_interval_seconds: Annotated[
        float,
        BeforeValidator(_reject_bool_as_number),
    ] = Field(
        default=1.0,
        gt=0,
    )
    worker_lease_seconds: Annotated[
        int,
        BeforeValidator(_reject_bool_as_number),
    ] = Field(
        default=300,
        gt=0,
    )
    worker_stale_recovery_interval_seconds: Annotated[
        int,
        BeforeValidator(_reject_bool_as_number),
    ] = Field(
        default=60,
        gt=0,
    )
    worker_stale_recovery_batch_size: Annotated[
        int,
        BeforeValidator(_reject_bool_as_number),
    ] = Field(
        default=50,
        ge=1,
        le=100,
    )
    worker_retry_base_delay_seconds: Annotated[
        int,
        BeforeValidator(_reject_bool_as_number),
    ] = Field(
        default=30,
        gt=0,
    )
    worker_retry_maximum_delay_seconds: Annotated[
        int,
        BeforeValidator(_reject_bool_as_number),
    ] = Field(
        default=3600,
        gt=0,
    )

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CLINICOPS_",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_secret_configuration(self) -> Self:
        """Reject weak or deployment-unsafe application secrets."""

        signing_key = self.auth_signing_key.get_secret_value()
        webhook_secret = self.billing_webhook_secret.get_secret_value()

        if len(signing_key.encode("utf-8")) < MINIMUM_AUTH_SIGNING_KEY_BYTES:
            raise ValueError(
                "Authentication signing key must contain "
                f"at least {MINIMUM_AUTH_SIGNING_KEY_BYTES} "
                "UTF-8 bytes."
            )

        if len(webhook_secret.encode("utf-8")) < MINIMUM_BILLING_WEBHOOK_SECRET_BYTES:
            raise ValueError(
                "Billing webhook secret must contain "
                f"at least "
                f"{MINIMUM_BILLING_WEBHOOK_SECRET_BYTES} "
                "UTF-8 bytes."
            )

        deployed_environments = {
            Environment.STAGING,
            Environment.PRODUCTION,
        }

        if self.environment in deployed_environments:
            if signing_key == DEFAULT_LOCAL_AUTH_SIGNING_KEY:
                raise ValueError(
                    "The default local authentication "
                    "signing key cannot be used in a "
                    "deployed environment."
                )

            if webhook_secret == DEFAULT_LOCAL_BILLING_WEBHOOK_SECRET:
                raise ValueError(
                    "The default local billing webhook "
                    "secret cannot be used in a deployed "
                    "environment."
                )

        return self

    @model_validator(mode="after")
    def validate_worker_runtime_configuration(self) -> Self:
        """Enforce worker scheduling relationships across settings."""

        if self.worker_stale_recovery_interval_seconds > self.worker_lease_seconds:
            raise ValueError(
                "worker_stale_recovery_interval_seconds must not exceed worker_lease_seconds."
            )

        if self.worker_retry_maximum_delay_seconds < self.worker_retry_base_delay_seconds:
            raise ValueError(
                "worker_retry_maximum_delay_seconds must "
                "be greater than or equal to "
                "worker_retry_base_delay_seconds."
            )

        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
