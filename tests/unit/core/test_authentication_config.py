from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from clinicops.core.config import (
    DEFAULT_LOCAL_AUTH_SIGNING_KEY,
    Environment,
    Settings,
)
from tests.conftest import IsolatedSettings

SECURE_SIGNING_KEY = "test-signing-key-with-at-least-32-bytes"
SECURE_WEBHOOK_SECRET = "test-billing-webhook-secret-with-32-bytes"


def build_settings(**overrides: Any) -> Settings:
    """Build settings without reading the repository dotenv file."""

    return IsolatedSettings(**overrides)


def test_authentication_settings_have_safe_local_defaults() -> None:
    settings = build_settings()

    assert settings.auth_issuer == "clinicops"
    assert settings.auth_audience == "clinicops-api"
    assert settings.auth_signing_key.get_secret_value() == DEFAULT_LOCAL_AUTH_SIGNING_KEY
    assert DEFAULT_LOCAL_AUTH_SIGNING_KEY not in repr(settings)


def test_authentication_settings_load_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CLINICOPS_AUTH_ISSUER",
        "  clinicops-test  ",
    )
    monkeypatch.setenv(
        "CLINICOPS_AUTH_AUDIENCE",
        "  clinicops-test-api  ",
    )
    monkeypatch.setenv(
        "CLINICOPS_AUTH_SIGNING_KEY",
        SECURE_SIGNING_KEY,
    )

    settings = build_settings()

    assert settings.auth_issuer == "clinicops-test"
    assert settings.auth_audience == "clinicops-test-api"
    assert settings.auth_signing_key.get_secret_value() == SECURE_SIGNING_KEY
    assert SECURE_SIGNING_KEY not in repr(settings)


@pytest.mark.parametrize(
    "signing_key",
    [
        "",
        "short-signing-key",
        "á" * 15,
    ],
)
def test_authentication_settings_reject_short_signing_keys(
    signing_key: str,
) -> None:
    with pytest.raises(ValidationError):
        build_settings(auth_signing_key=SecretStr(signing_key))


@pytest.mark.parametrize(
    "environment",
    [
        Environment.STAGING,
        Environment.PRODUCTION,
    ],
)
def test_deployed_environment_rejects_default_local_signing_key(
    environment: Environment,
) -> None:
    with pytest.raises(ValidationError):
        build_settings(
            environment=environment,
            auth_signing_key=SecretStr(DEFAULT_LOCAL_AUTH_SIGNING_KEY),
        )


def test_production_accepts_explicit_secure_signing_key() -> None:
    settings = build_settings(
        environment=Environment.PRODUCTION,
        auth_signing_key=SecretStr(SECURE_SIGNING_KEY),
        billing_webhook_secret=SecretStr(SECURE_WEBHOOK_SECRET),
    )

    assert settings.environment is Environment.PRODUCTION
    assert settings.auth_signing_key.get_secret_value() == SECURE_SIGNING_KEY
