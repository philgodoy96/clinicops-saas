import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from clinicops.core.config import Environment, Settings


def test_settings_use_expected_defaults_without_env_file() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "ClinicOps SaaS"
    assert settings.app_version == "0.1.0"
    assert settings.environment is Environment.LOCAL
    assert settings.log_level == "INFO"
    assert settings.database_url == (
        "postgresql+psycopg://clinicops:clinicops@localhost:5432/clinicops"
    )
    assert settings.database_connect_timeout_seconds == 3


def test_settings_load_prefixed_environment_variables(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLINICOPS_APP_NAME", "ClinicOps Test")
    monkeypatch.setenv("CLINICOPS_ENVIRONMENT", "test")
    monkeypatch.setenv("CLINICOPS_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv(
        "CLINICOPS_DATABASE_URL",
        "postgresql+psycopg://test:test@localhost:5432/clinicops_test",
    )
    monkeypatch.setenv("CLINICOPS_DATABASE_CONNECT_TIMEOUT_SECONDS", "7")

    settings = Settings(_env_file=None)

    assert settings.app_name == "ClinicOps Test"
    assert settings.environment is Environment.TEST
    assert settings.log_level == "DEBUG"
    assert settings.database_url == ("postgresql+psycopg://test:test@localhost:5432/clinicops_test")
    assert settings.database_connect_timeout_seconds == 7


@pytest.mark.parametrize("timeout", [0, 31])
def test_settings_reject_invalid_database_connect_timeout(timeout: int) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_connect_timeout_seconds=timeout,
        )
