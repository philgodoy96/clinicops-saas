from collections.abc import Callable

import pytest
from pydantic import ValidationError

from clinicops.core.config import (
    DEFAULT_LOCAL_BILLING_WEBHOOK_SECRET,
    Environment,
    Settings,
)

SECURE_WEBHOOK_SECRET = "test-billing-webhook-secret-with-32-bytes"
SECURE_SIGNING_KEY = "test-auth-signing-key-with-at-least-32-bytes"


def test_billing_webhook_settings_have_local_defaults(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()

    assert (
        settings.billing_webhook_secret.get_secret_value() == DEFAULT_LOCAL_BILLING_WEBHOOK_SECRET
    )
    assert settings.billing_webhook_signature_tolerance_seconds == 300
    assert settings.billing_webhook_max_payload_bytes == 256 * 1024
    assert DEFAULT_LOCAL_BILLING_WEBHOOK_SECRET not in repr(settings)


def test_billing_webhook_settings_load_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
    settings_factory: Callable[..., Settings],
) -> None:
    monkeypatch.setenv(
        "CLINICOPS_BILLING_WEBHOOK_SECRET",
        SECURE_WEBHOOK_SECRET,
    )
    monkeypatch.setenv(
        ("CLINICOPS_BILLING_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS"),
        "120",
    )
    monkeypatch.setenv(
        ("CLINICOPS_BILLING_WEBHOOK_MAX_PAYLOAD_BYTES"),
        "4096",
    )

    settings = settings_factory()

    assert settings.billing_webhook_secret.get_secret_value() == SECURE_WEBHOOK_SECRET
    assert settings.billing_webhook_signature_tolerance_seconds == 120
    assert settings.billing_webhook_max_payload_bytes == 4096


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "short-secret",
        "á" * 15,
    ],
)
def test_billing_webhook_settings_reject_short_secret(
    secret: str,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(
            billing_webhook_secret=secret,
        )


@pytest.mark.parametrize(
    "environment",
    [
        Environment.STAGING,
        Environment.PRODUCTION,
    ],
)
def test_deployed_environment_rejects_local_webhook_secret(
    environment: Environment,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(
            environment=environment,
            auth_signing_key=SECURE_SIGNING_KEY,
            billing_webhook_secret=(DEFAULT_LOCAL_BILLING_WEBHOOK_SECRET),
        )


def test_production_accepts_explicit_webhook_secret(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        environment=Environment.PRODUCTION,
        auth_signing_key=SECURE_SIGNING_KEY,
        billing_webhook_secret=SECURE_WEBHOOK_SECRET,
    )

    assert settings.billing_webhook_secret.get_secret_value() == SECURE_WEBHOOK_SECRET


@pytest.mark.parametrize(
    (
        "field_name",
        "value",
    ),
    [
        (
            "billing_webhook_signature_tolerance_seconds",
            -1,
        ),
        (
            "billing_webhook_signature_tolerance_seconds",
            3601,
        ),
        (
            "billing_webhook_max_payload_bytes",
            0,
        ),
        (
            "billing_webhook_max_payload_bytes",
            1024 * 1024 + 1,
        ),
    ],
)
def test_billing_webhook_settings_reject_invalid_bounds(
    field_name: str,
    value: int,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(
            **{field_name: value},
        )
