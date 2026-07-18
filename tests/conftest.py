from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from clinicops.core.config import Environment, Settings
from clinicops.main import create_app


@pytest.fixture
def app_settings() -> Settings:
    """Provide isolated application settings for HTTP tests."""

    return Settings(
        _env_file=None,
        environment=Environment.TEST,
    )


@pytest.fixture
def client(app_settings: Settings) -> Iterator[TestClient]:
    """Provide an HTTP client for the configured FastAPI application."""

    with TestClient(create_app(app_settings)) as test_client:
        yield test_client
