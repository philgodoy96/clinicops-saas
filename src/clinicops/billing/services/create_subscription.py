from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from clinicops.billing.catalog import get_price_definition
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.idempotency_keys import (
    validate_idempotency_key,
)


@dataclass(frozen=True, slots=True)
class CreateBillingSubscriptionCommand:
    """Validated input for the tenant subscription-creation workflow."""

    tenant_id: UUID
    price_code: str
    idempotency_key: str

    def __post_init__(self) -> None:
        normalized_price_code = self.price_code.strip()
        price = get_price_definition(normalized_price_code)

        object.__setattr__(
            self,
            "price_code",
            price.price_code,
        )
        object.__setattr__(
            self,
            "idempotency_key",
            validate_idempotency_key(self.idempotency_key),
        )


@dataclass(frozen=True, slots=True)
class CreatedBillingSubscription:
    """Public application result for a created or replayed subscription."""

    id: UUID
    tenant_id: UUID
    price_code: str
    plan: BillingPlan
    billing_interval: BillingInterval
    currency: str
    unit_amount: int
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    cancellation_requested_at: datetime | None
    canceled_at: datetime | None
    pending_price_code: str | None
    created_at: datetime
    updated_at: datetime
    replayed: bool

    def __post_init__(self) -> None:
        normalized_price_code = self.price_code.strip()
        normalized_currency = self.currency.strip().upper()

        if not normalized_price_code:
            raise ValueError("price_code must not be empty.")

        if not normalized_currency:
            raise ValueError("currency must not be empty.")

        if self.unit_amount <= 0:
            raise ValueError("unit_amount must be greater than zero.")

        period_start = _normalize_datetime(
            self.current_period_start,
            field_name="current_period_start",
        )
        period_end = _normalize_datetime(
            self.current_period_end,
            field_name="current_period_end",
        )

        if period_start >= period_end:
            raise ValueError("current_period_start must be earlier than current_period_end.")

        cancellation_requested_at = _normalize_optional_datetime(
            self.cancellation_requested_at,
            field_name="cancellation_requested_at",
        )
        canceled_at = _normalize_optional_datetime(
            self.canceled_at,
            field_name="canceled_at",
        )
        created_at = _normalize_datetime(
            self.created_at,
            field_name="created_at",
        )
        updated_at = _normalize_datetime(
            self.updated_at,
            field_name="updated_at",
        )

        if updated_at < created_at:
            raise ValueError("updated_at must not be earlier than created_at.")

        pending_price_code = (
            self.pending_price_code.strip() if self.pending_price_code is not None else None
        )

        if pending_price_code == "":
            raise ValueError("pending_price_code must not be empty.")

        object.__setattr__(
            self,
            "price_code",
            normalized_price_code,
        )
        object.__setattr__(
            self,
            "currency",
            normalized_currency,
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
            "cancellation_requested_at",
            cancellation_requested_at,
        )
        object.__setattr__(
            self,
            "canceled_at",
            canceled_at,
        )
        object.__setattr__(
            self,
            "pending_price_code",
            pending_price_code,
        )
        object.__setattr__(
            self,
            "created_at",
            created_at,
        )
        object.__setattr__(
            self,
            "updated_at",
            updated_at,
        )


def _normalize_optional_datetime(
    value: datetime | None,
    *,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None

    return _normalize_datetime(
        value,
        field_name=field_name,
    )


def _normalize_datetime(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")

    return value.astimezone(UTC)
