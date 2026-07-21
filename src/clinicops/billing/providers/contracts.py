from dataclasses import dataclass
from datetime import UTC, datetime

from clinicops.billing.providers.idempotency import (
    validate_provider_operation_key,
)


@dataclass(frozen=True, slots=True)
class CreateCustomerRequest:
    """Provider request for creating a customer."""

    provider_operation_key: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_operation_key",
            validate_provider_operation_key(self.provider_operation_key),
        )


@dataclass(frozen=True, slots=True)
class CreateCustomerResult:
    """Provider-confirmed customer creation result."""

    provider_customer_id: str
    provider_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_customer_id",
            _normalize_non_empty(
                self.provider_customer_id,
                field_name="provider_customer_id",
            ),
        )
        object.__setattr__(
            self,
            "provider_reference",
            _normalize_non_empty(
                self.provider_reference,
                field_name="provider_reference",
            ),
        )


@dataclass(frozen=True, slots=True)
class CreateSubscriptionRequest:
    """Provider request for creating a subscription."""

    provider_operation_key: str
    provider_customer_id: str
    price_code: str
    effective_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_operation_key",
            validate_provider_operation_key(self.provider_operation_key),
        )
        object.__setattr__(
            self,
            "provider_customer_id",
            _normalize_non_empty(
                self.provider_customer_id,
                field_name="provider_customer_id",
            ),
        )
        object.__setattr__(
            self,
            "price_code",
            _normalize_non_empty(
                self.price_code,
                field_name="price_code",
            ),
        )
        object.__setattr__(
            self,
            "effective_at",
            _normalize_datetime(
                self.effective_at,
                field_name="effective_at",
            ),
        )


@dataclass(frozen=True, slots=True)
class CreateSubscriptionResult:
    """Provider-confirmed subscription creation result."""

    provider_subscription_id: str
    provider_state_version: int
    current_period_start: datetime
    current_period_end: datetime
    provider_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_subscription_id",
            _normalize_non_empty(
                self.provider_subscription_id,
                field_name="provider_subscription_id",
            ),
        )
        _require_nonnegative_version(self.provider_state_version)
        period_start = _normalize_datetime(
            self.current_period_start,
            field_name="current_period_start",
        )
        period_end = _normalize_datetime(
            self.current_period_end,
            field_name="current_period_end",
        )
        _require_ordered_period(
            period_start=period_start,
            period_end=period_end,
        )
        object.__setattr__(
            self,
            "current_period_start",
            period_start,
        )
        object.__setattr__(
            self,
            "current_period_end",
            period_end,
        )
        object.__setattr__(
            self,
            "provider_reference",
            _normalize_non_empty(
                self.provider_reference,
                field_name="provider_reference",
            ),
        )


@dataclass(frozen=True, slots=True)
class ChangePlanRequest:
    """Provider request for changing a subscription price."""

    provider_operation_key: str
    provider_subscription_id: str
    target_price_code: str
    effective_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_operation_key",
            validate_provider_operation_key(self.provider_operation_key),
        )
        object.__setattr__(
            self,
            "provider_subscription_id",
            _normalize_non_empty(
                self.provider_subscription_id,
                field_name="provider_subscription_id",
            ),
        )
        object.__setattr__(
            self,
            "target_price_code",
            _normalize_non_empty(
                self.target_price_code,
                field_name="target_price_code",
            ),
        )
        object.__setattr__(
            self,
            "effective_at",
            _normalize_datetime(
                self.effective_at,
                field_name="effective_at",
            ),
        )


@dataclass(frozen=True, slots=True)
class ChangePlanResult:
    """Provider-confirmed subscription price-change result."""

    provider_subscription_id: str
    provider_state_version: int
    effective_price_code: str
    current_period_start: datetime
    current_period_end: datetime
    provider_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_subscription_id",
            _normalize_non_empty(
                self.provider_subscription_id,
                field_name="provider_subscription_id",
            ),
        )
        _require_nonnegative_version(self.provider_state_version)
        object.__setattr__(
            self,
            "effective_price_code",
            _normalize_non_empty(
                self.effective_price_code,
                field_name="effective_price_code",
            ),
        )
        period_start = _normalize_datetime(
            self.current_period_start,
            field_name="current_period_start",
        )
        period_end = _normalize_datetime(
            self.current_period_end,
            field_name="current_period_end",
        )
        _require_ordered_period(
            period_start=period_start,
            period_end=period_end,
        )
        object.__setattr__(
            self,
            "current_period_start",
            period_start,
        )
        object.__setattr__(
            self,
            "current_period_end",
            period_end,
        )
        object.__setattr__(
            self,
            "provider_reference",
            _normalize_non_empty(
                self.provider_reference,
                field_name="provider_reference",
            ),
        )


@dataclass(frozen=True, slots=True)
class CancelSubscriptionRequest:
    """Provider request for canceling a subscription."""

    provider_operation_key: str
    provider_subscription_id: str
    effective_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_operation_key",
            validate_provider_operation_key(self.provider_operation_key),
        )
        object.__setattr__(
            self,
            "provider_subscription_id",
            _normalize_non_empty(
                self.provider_subscription_id,
                field_name="provider_subscription_id",
            ),
        )
        object.__setattr__(
            self,
            "effective_at",
            _normalize_datetime(
                self.effective_at,
                field_name="effective_at",
            ),
        )


@dataclass(frozen=True, slots=True)
class CancelSubscriptionResult:
    """Provider-confirmed subscription cancellation result."""

    provider_subscription_id: str
    provider_state_version: int
    canceled_at: datetime
    provider_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_subscription_id",
            _normalize_non_empty(
                self.provider_subscription_id,
                field_name="provider_subscription_id",
            ),
        )
        _require_nonnegative_version(self.provider_state_version)
        object.__setattr__(
            self,
            "canceled_at",
            _normalize_datetime(
                self.canceled_at,
                field_name="canceled_at",
            ),
        )
        object.__setattr__(
            self,
            "provider_reference",
            _normalize_non_empty(
                self.provider_reference,
                field_name="provider_reference",
            ),
        )


def _normalize_non_empty(
    value: str,
    *,
    field_name: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")

    return normalized


def _normalize_datetime(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")

    return value.astimezone(UTC)


def _require_nonnegative_version(
    provider_state_version: int,
) -> None:
    if provider_state_version < 0:
        raise ValueError("provider_state_version must not be negative.")


def _require_ordered_period(
    *,
    period_start: datetime,
    period_end: datetime,
) -> None:
    if period_start >= period_end:
        raise ValueError("current_period_start must be earlier than current_period_end.")
