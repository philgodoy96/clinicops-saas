from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from clinicops.billing.enums import BillingInterval
from clinicops.billing.providers.periods import (
    BillingPeriod,
    calculate_billing_period,
)


def test_monthly_period_uses_the_calendar_anniversary() -> None:
    period = calculate_billing_period(
        effective_at=datetime(
            2026,
            7,
            21,
            14,
            30,
            15,
            123456,
            tzinfo=UTC,
        ),
        billing_interval=BillingInterval.MONTHLY,
    )

    assert period.start == datetime(
        2026,
        7,
        21,
        14,
        30,
        15,
        123456,
        tzinfo=UTC,
    )
    assert period.end == datetime(
        2026,
        8,
        21,
        14,
        30,
        15,
        123456,
        tzinfo=UTC,
    )


def test_monthly_period_crosses_the_year_boundary() -> None:
    period = calculate_billing_period(
        effective_at=datetime(
            2026,
            12,
            15,
            10,
            tzinfo=UTC,
        ),
        billing_interval=BillingInterval.MONTHLY,
    )

    assert period.end == datetime(
        2027,
        1,
        15,
        10,
        tzinfo=UTC,
    )


def test_monthly_period_clamps_to_non_leap_february() -> None:
    period = calculate_billing_period(
        effective_at=datetime(
            2026,
            1,
            31,
            9,
            tzinfo=UTC,
        ),
        billing_interval=BillingInterval.MONTHLY,
    )

    assert period.end == datetime(
        2026,
        2,
        28,
        9,
        tzinfo=UTC,
    )


def test_monthly_period_clamps_to_leap_day() -> None:
    period = calculate_billing_period(
        effective_at=datetime(
            2028,
            1,
            31,
            9,
            tzinfo=UTC,
        ),
        billing_interval=BillingInterval.MONTHLY,
    )

    assert period.end == datetime(
        2028,
        2,
        29,
        9,
        tzinfo=UTC,
    )


def test_yearly_period_uses_the_calendar_anniversary() -> None:
    period = calculate_billing_period(
        effective_at=datetime(
            2026,
            7,
            21,
            14,
            30,
            tzinfo=UTC,
        ),
        billing_interval=BillingInterval.YEARLY,
    )

    assert period.end == datetime(
        2027,
        7,
        21,
        14,
        30,
        tzinfo=UTC,
    )


def test_yearly_period_clamps_leap_day_in_a_non_leap_year() -> None:
    period = calculate_billing_period(
        effective_at=datetime(
            2028,
            2,
            29,
            12,
            tzinfo=UTC,
        ),
        billing_interval=BillingInterval.YEARLY,
    )

    assert period.end == datetime(
        2029,
        2,
        28,
        12,
        tzinfo=UTC,
    )


def test_period_normalizes_to_utc_before_calendar_arithmetic() -> None:
    source_timezone = timezone(-timedelta(hours=3))

    period = calculate_billing_period(
        effective_at=datetime(
            2026,
            1,
            31,
            23,
            30,
            tzinfo=source_timezone,
        ),
        billing_interval=BillingInterval.MONTHLY,
    )

    assert period.start == datetime(
        2026,
        2,
        1,
        2,
        30,
        tzinfo=UTC,
    )
    assert period.end == datetime(
        2026,
        3,
        1,
        2,
        30,
        tzinfo=UTC,
    )
    assert period.start.tzinfo is UTC
    assert period.end.tzinfo is UTC


@pytest.mark.parametrize(
    "billing_interval",
    [
        BillingInterval.MONTHLY,
        BillingInterval.YEARLY,
    ],
)
def test_period_calculation_rejects_naive_effective_at(
    billing_interval: BillingInterval,
) -> None:
    with pytest.raises(
        ValueError,
        match="effective_at must be timezone-aware",
    ):
        calculate_billing_period(
            effective_at=datetime(2026, 7, 21),
            billing_interval=billing_interval,
        )


def test_billing_period_is_immutable() -> None:
    period = BillingPeriod(
        start=datetime(
            2026,
            7,
            21,
            tzinfo=UTC,
        ),
        end=datetime(
            2026,
            8,
            21,
            tzinfo=UTC,
        ),
    )

    with pytest.raises(FrozenInstanceError):
        period.end = datetime(  # type: ignore[misc]
            2026,
            9,
            21,
            tzinfo=UTC,
        )


def test_billing_period_normalizes_boundaries_to_utc() -> None:
    source_timezone = timezone(timedelta(hours=2))

    period = BillingPeriod(
        start=datetime(
            2026,
            7,
            21,
            12,
            tzinfo=source_timezone,
        ),
        end=datetime(
            2026,
            8,
            21,
            12,
            tzinfo=source_timezone,
        ),
    )

    assert period.start == datetime(
        2026,
        7,
        21,
        10,
        tzinfo=UTC,
    )
    assert period.end == datetime(
        2026,
        8,
        21,
        10,
        tzinfo=UTC,
    )


def test_billing_period_rejects_unordered_boundaries() -> None:
    boundary = datetime(
        2026,
        7,
        21,
        tzinfo=UTC,
    )

    with pytest.raises(
        ValueError,
        match="start must be earlier than end",
    ):
        BillingPeriod(
            start=boundary,
            end=boundary,
        )
