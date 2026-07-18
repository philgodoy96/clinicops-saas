from collections.abc import Callable, Iterator
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict

from clinicops.core.config import Environment, Settings
from clinicops.main import create_app


class IsolatedSettings(Settings):
    """Settings variant that ignores local dotenv files during tests."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=None,
        env_prefix="CLINICOPS_",
        case_sensitive=False,
        extra="ignore",
    )


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    """Build validated settings without reading the local dotenv file."""

    def factory(**overrides: Any) -> Settings:
        return IsolatedSettings(**overrides)

    return factory


@pytest.fixture
def app_settings(settings_factory: Callable[..., Settings]) -> Settings:
    """Provide isolated application settings for HTTP tests."""

    return settings_factory(environment=Environment.TEST)


@pytest.fixture
def client(app_settings: Settings) -> Iterator[TestClient]:
    """Provide an HTTP client for the configured FastAPI application."""

    with TestClient(create_app(app_settings)) as test_client:
        yield test_client
