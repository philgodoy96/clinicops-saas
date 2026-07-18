from enum import StrEnum
from functools import lru_cache
from typing import ClassVar, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported application runtime environments."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Validated application configuration loaded from the environment."""

    app_name: str = "ClinicOps SaaS"
    app_version: str = "0.1.0"
    environment: Environment = Environment.LOCAL
    log_level: LogLevel = "INFO"

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CLINICOPS_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
