from collections.abc import Callable

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from clinicops.core.config import Environment, Settings

CLINICOPS_ENVIRONMENT_VARIABLES = (
    "CLINICOPS_APP_NAME",
    "CLINICOPS_APP_VERSION",
    "CLINICOPS_ENVIRONMENT",
    "CLINICOPS_LOG_LEVEL",
    "CLINICOPS_DATABASE_URL",
    "CLINICOPS_DATABASE_CONNECT_TIMEOUT_SECONDS",
    "CLINICOPS_AUTH_ISSUER",
    "CLINICOPS_AUTH_AUDIENCE",
    "CLINICOPS_AUTH_SIGNING_KEY",
    "CLINICOPS_BILLING_WEBHOOK_SECRET",
    "CLINICOPS_BILLING_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS",
    "CLINICOPS_BILLING_WEBHOOK_MAX_PAYLOAD_BYTES",
    "CLINICOPS_WORKER_ID",
    "CLINICOPS_WORKER_POLL_INTERVAL_SECONDS",
    "CLINICOPS_WORKER_LEASE_SECONDS",
    "CLINICOPS_WORKER_STALE_RECOVERY_INTERVAL_SECONDS",
    "CLINICOPS_WORKER_STALE_RECOVERY_BATCH_SIZE",
    "CLINICOPS_WORKER_RETRY_BASE_DELAY_SECONDS",
    "CLINICOPS_WORKER_RETRY_MAXIMUM_DELAY_SECONDS",
)


def clear_clinicops_environment(monkeypatch: MonkeyPatch) -> None:
    """Remove ClinicOps variables that could affect settings tests."""

    for variable_name in CLINICOPS_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable_name, raising=False)


def test_settings_use_expected_defaults_without_env_file(
    monkeypatch: MonkeyPatch,
    settings_factory: Callable[..., Settings],
) -> None:
    clear_clinicops_environment(monkeypatch)

    settings = settings_factory()

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
    settings_factory: Callable[..., Settings],
) -> None:
    clear_clinicops_environment(monkeypatch)
    monkeypatch.setenv("CLINICOPS_APP_NAME", "ClinicOps Test")
    monkeypatch.setenv("CLINICOPS_ENVIRONMENT", "test")
    monkeypatch.setenv("CLINICOPS_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv(
        "CLINICOPS_DATABASE_URL",
        "postgresql+psycopg://test:test@localhost:5432/clinicops_test",
    )
    monkeypatch.setenv("CLINICOPS_DATABASE_CONNECT_TIMEOUT_SECONDS", "7")

    settings = settings_factory()

    assert settings.app_name == "ClinicOps Test"
    assert settings.environment is Environment.TEST
    assert settings.log_level == "DEBUG"
    assert settings.database_url == ("postgresql+psycopg://test:test@localhost:5432/clinicops_test")
    assert settings.database_connect_timeout_seconds == 7


@pytest.mark.parametrize("timeout", [0, 31])
def test_settings_reject_invalid_database_connect_timeout(
    timeout: int,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(database_connect_timeout_seconds=timeout)


def test_worker_runtime_settings_use_expected_defaults(
    monkeypatch: MonkeyPatch,
    settings_factory: Callable[..., Settings],
) -> None:
    clear_clinicops_environment(monkeypatch)

    settings = settings_factory()

    assert settings.worker_id is None
    assert settings.worker_poll_interval_seconds == 1.0
    assert settings.worker_lease_seconds == 300
    assert settings.worker_stale_recovery_interval_seconds == 60
    assert settings.worker_stale_recovery_batch_size == 50
    assert settings.worker_retry_base_delay_seconds == 30
    assert settings.worker_retry_maximum_delay_seconds == 3600


def test_worker_runtime_settings_load_prefixed_environment(
    monkeypatch: MonkeyPatch,
    settings_factory: Callable[..., Settings],
) -> None:
    clear_clinicops_environment(monkeypatch)
    monkeypatch.setenv("CLINICOPS_WORKER_ID", "worker-alpha")
    monkeypatch.setenv("CLINICOPS_WORKER_POLL_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("CLINICOPS_WORKER_LEASE_SECONDS", "120")
    monkeypatch.setenv(
        "CLINICOPS_WORKER_STALE_RECOVERY_INTERVAL_SECONDS",
        "30",
    )
    monkeypatch.setenv(
        "CLINICOPS_WORKER_STALE_RECOVERY_BATCH_SIZE",
        "25",
    )
    monkeypatch.setenv(
        "CLINICOPS_WORKER_RETRY_BASE_DELAY_SECONDS",
        "15",
    )
    monkeypatch.setenv(
        "CLINICOPS_WORKER_RETRY_MAXIMUM_DELAY_SECONDS",
        "900",
    )

    settings = settings_factory()

    assert settings.worker_id == "worker-alpha"
    assert settings.worker_poll_interval_seconds == 2.5
    assert settings.worker_lease_seconds == 120
    assert settings.worker_stale_recovery_interval_seconds == 30
    assert settings.worker_stale_recovery_batch_size == 25
    assert settings.worker_retry_base_delay_seconds == 15
    assert settings.worker_retry_maximum_delay_seconds == 900


def test_worker_id_strips_surrounding_whitespace(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(worker_id="  worker-beta  ")

    assert settings.worker_id == "worker-beta"


@pytest.mark.parametrize("worker_id", ["", " ", "\t", "\n"])
def test_worker_id_rejects_blank_values(
    worker_id: str,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(worker_id=worker_id)


def test_worker_id_rejects_oversized_values(
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(worker_id="w" * 256)


@pytest.mark.parametrize("poll_interval", [0, -1, 0.0, -0.5])
def test_worker_poll_interval_rejects_non_positive_values(
    poll_interval: float,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(worker_poll_interval_seconds=poll_interval)


@pytest.mark.parametrize("lease_seconds", [0, -1])
def test_worker_lease_rejects_non_positive_values(
    lease_seconds: int,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(worker_lease_seconds=lease_seconds)


def test_worker_stale_recovery_interval_rejects_above_lease(
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(
            worker_lease_seconds=60,
            worker_stale_recovery_interval_seconds=61,
        )


@pytest.mark.parametrize("batch_size", [0, -1, 101])
def test_worker_stale_recovery_batch_size_rejects_out_of_range(
    batch_size: int,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(
            worker_stale_recovery_batch_size=batch_size,
        )


@pytest.mark.parametrize("base_delay", [0, -1])
def test_worker_retry_base_delay_rejects_non_positive_values(
    base_delay: int,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(
            worker_retry_base_delay_seconds=base_delay,
        )


def test_worker_retry_maximum_delay_rejects_below_base_delay(
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(
            worker_retry_base_delay_seconds=60,
            worker_retry_maximum_delay_seconds=59,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("worker_poll_interval_seconds", True),
        ("worker_poll_interval_seconds", False),
        ("worker_lease_seconds", True),
        ("worker_lease_seconds", False),
        ("worker_stale_recovery_interval_seconds", True),
        ("worker_stale_recovery_interval_seconds", False),
        ("worker_stale_recovery_batch_size", True),
        ("worker_stale_recovery_batch_size", False),
        ("worker_retry_base_delay_seconds", True),
        ("worker_retry_base_delay_seconds", False),
        ("worker_retry_maximum_delay_seconds", True),
        ("worker_retry_maximum_delay_seconds", False),
    ],
)
def test_worker_runtime_settings_reject_booleans(
    field_name: str,
    value: bool,
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(**{field_name: value})
