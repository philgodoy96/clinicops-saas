from datetime import timedelta

import pytest

from clinicops.jobs.exceptions import (
    BackgroundJobInvalidConfigurationError,
)
from clinicops.jobs.retry import BackgroundJobRetryPolicy


def test_retry_policy_uses_equal_jitter_lower_boundary() -> None:
    policy = BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
        random_value_provider=lambda: 0.0,
    )

    delay = policy.calculate_delay(attempt_number=1)

    assert delay == timedelta(seconds=30)


def test_retry_policy_uses_equal_jitter_upper_boundary() -> None:
    policy = BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
        random_value_provider=lambda: 1.0,
    )

    delay = policy.calculate_delay(attempt_number=1)

    assert delay == timedelta(seconds=60)


def test_retry_policy_applies_exponential_growth() -> None:
    policy = BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
        random_value_provider=lambda: 1.0,
    )

    assert policy.calculate_delay(attempt_number=1) == timedelta(seconds=60)
    assert policy.calculate_delay(attempt_number=2) == timedelta(seconds=120)
    assert policy.calculate_delay(attempt_number=3) == timedelta(seconds=240)


def test_retry_policy_caps_exponential_delay() -> None:
    policy = BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(minutes=5),
        random_value_provider=lambda: 1.0,
    )

    delay = policy.calculate_delay(attempt_number=20)

    assert delay == timedelta(minutes=5)


@pytest.mark.parametrize(
    ("base_delay", "maximum_delay"),
    [
        (
            timedelta(0),
            timedelta(minutes=5),
        ),
        (
            timedelta(seconds=-1),
            timedelta(minutes=5),
        ),
        (
            timedelta(minutes=10),
            timedelta(minutes=5),
        ),
    ],
)
def test_retry_policy_rejects_invalid_delays(
    base_delay: timedelta,
    maximum_delay: timedelta,
) -> None:
    with pytest.raises(BackgroundJobInvalidConfigurationError):
        BackgroundJobRetryPolicy(
            base_delay=base_delay,
            maximum_delay=maximum_delay,
        )


@pytest.mark.parametrize(
    "attempt_number",
    [
        0,
        -1,
        True,
    ],
)
def test_retry_policy_rejects_invalid_attempt_number(
    attempt_number: int,
) -> None:
    policy = BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
    )

    with pytest.raises(BackgroundJobInvalidConfigurationError):
        policy.calculate_delay(attempt_number=attempt_number)


@pytest.mark.parametrize(
    "random_value",
    [
        -0.1,
        1.1,
        True,
    ],
)
def test_retry_policy_rejects_invalid_random_value(
    random_value: float,
) -> None:
    policy = BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
        random_value_provider=lambda: random_value,
    )

    with pytest.raises(BackgroundJobInvalidConfigurationError):
        policy.calculate_delay(attempt_number=1)
