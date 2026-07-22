from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from clinicops.billing.enums import BillingInterval

_MONTHS_PER_YEAR: Final = 12


@dataclass(frozen=True, slots=True)
class BillingPeriod:
    """Provider-confirmed billing period boundaries."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        normalized_start = _normalize_datetime(
            self.start,
            field_name="start",
        )
        normalized_end = _normalize_datetime(
            self.end,
            field_name="end",
        )

        if normalized_start >= normalized_end:
            raise ValueError("Billing period start must be earlier than end.")

        object.__setattr__(
            self,
            "start",
            normalized_start,
        )
        object.__setattr__(
            self,
            "end",
            normalized_end,
        )


def calculate_billing_period(
    *,
    effective_at: datetime,
    billing_interval: BillingInterval,
) -> BillingPeriod:
    """Calculate one calendar-accurate provider billing period."""

    period_start = _normalize_datetime(
        effective_at,
        field_name="effective_at",
    )

    if billing_interval is BillingInterval.MONTHLY:
        period_end = _add_calendar_months(
            period_start,
            months=1,
        )
    elif billing_interval is BillingInterval.YEARLY:
        period_end = _add_calendar_years(
            period_start,
            years=1,
        )
    else:
        raise ValueError("Unsupported billing interval.")

    return BillingPeriod(
        start=period_start,
        end=period_end,
    )


def _add_calendar_months(
    value: datetime,
    *,
    months: int,
) -> datetime:
    if months < 0:
        raise ValueError("months must not be negative.")

    absolute_month = value.year * _MONTHS_PER_YEAR + value.month - 1 + months
    target_year, zero_based_month = divmod(
        absolute_month,
        _MONTHS_PER_YEAR,
    )
    target_month = zero_based_month + 1
    target_day = min(
        value.day,
        monthrange(target_year, target_month)[1],
    )

    return value.replace(
        year=target_year,
        month=target_month,
        day=target_day,
    )


def _add_calendar_years(
    value: datetime,
    *,
    years: int,
) -> datetime:
    if years < 0:
        raise ValueError("years must not be negative.")

    target_year = value.year + years
    target_day = min(
        value.day,
        monthrange(target_year, value.month)[1],
    )

    return value.replace(
        year=target_year,
        day=target_day,
    )


def _normalize_datetime(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")

    return value.astimezone(UTC)
