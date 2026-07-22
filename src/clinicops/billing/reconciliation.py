from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from clinicops.billing.enums import (
    BillingProvider,
    BillingReconciliationOutcome,
    SubscriptionStatus,
)


@dataclass(frozen=True, slots=True)
class ReconcileBillingSubscriptionCommand:
    """Identify one local subscription to reconcile."""

    subscription_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.subscription_id, UUID):
            raise TypeError("The billing subscription ID must be a UUID.")


@dataclass(frozen=True, slots=True)
class BillingProviderSubscriptionSnapshot:
    """Canonical provider-owned subscription state."""

    provider: BillingProvider
    provider_subscription_id: str
    provider_state_version: int
    price_code: str
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    canceled_at: datetime | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.provider, BillingProvider):
            raise TypeError("The billing provider must be a BillingProvider.")

        if not isinstance(self.status, SubscriptionStatus):
            raise TypeError("The provider subscription status must be a SubscriptionStatus.")

        normalized_provider_subscription_id = self.provider_subscription_id.strip()
        normalized_price_code = self.price_code.strip()

        if not normalized_provider_subscription_id:
            raise ValueError("The provider subscription ID must not be empty.")

        if not normalized_price_code:
            raise ValueError("The provider price code must not be empty.")

        object.__setattr__(
            self,
            "provider_subscription_id",
            normalized_provider_subscription_id,
        )
        object.__setattr__(
            self,
            "price_code",
            normalized_price_code,
        )

        if self.provider_state_version < 1:
            raise ValueError("The provider state version must be positive.")

        _require_timezone_aware(
            self.current_period_start,
            field_name="current_period_start",
        )
        _require_timezone_aware(
            self.current_period_end,
            field_name="current_period_end",
        )
        _require_timezone_aware(
            self.observed_at,
            field_name="observed_at",
        )

        if self.current_period_end <= self.current_period_start:
            raise ValueError("The provider billing period end must be after its start.")

        supported_statuses = {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.CANCELED,
        }

        if self.status not in supported_statuses:
            raise ValueError(
                "The provider subscription snapshot status is not supported for reconciliation."
            )

        if self.status is SubscriptionStatus.ACTIVE:
            if self.canceled_at is not None:
                raise ValueError(
                    "An active provider subscription snapshot must not include canceled_at."
                )
            return

        if self.cancel_at_period_end:
            raise ValueError(
                "A canceled provider subscription snapshot "
                "must not remain scheduled for cancellation."
            )

        if self.canceled_at is None:
            raise ValueError("A canceled provider subscription snapshot must include canceled_at.")

        _require_timezone_aware(
            self.canceled_at,
            field_name="canceled_at",
        )

        if not (self.current_period_start <= self.canceled_at <= self.current_period_end):
            raise ValueError(
                "The provider cancellation timestamp must fall inside the billing period."
            )


@dataclass(frozen=True, slots=True)
class ReconciledBillingSubscription:
    """Completed reconciliation comparison and repair result."""

    subscription_id: UUID
    provider: BillingProvider
    provider_subscription_id: str
    outcome: BillingReconciliationOutcome
    drift_fields: tuple[str, ...]
    previous_provider_state_version: int
    provider_state_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.subscription_id, UUID):
            raise TypeError("The reconciled subscription ID must be a UUID.")

        if not isinstance(self.provider, BillingProvider):
            raise TypeError("The reconciled provider must be a BillingProvider.")

        normalized_provider_subscription_id = self.provider_subscription_id.strip()

        if not normalized_provider_subscription_id:
            raise ValueError("The provider subscription ID must not be empty.")

        object.__setattr__(
            self,
            "provider_subscription_id",
            normalized_provider_subscription_id,
        )

        if self.previous_provider_state_version < 1:
            raise ValueError("The previous provider state version must be positive.")

        if self.provider_state_version < 1:
            raise ValueError("The provider state version must be positive.")

        normalized_drift_fields = tuple(field_name.strip() for field_name in self.drift_fields)

        if any(not field_name for field_name in normalized_drift_fields):
            raise ValueError("Reconciliation drift fields must not be empty.")

        if len(set(normalized_drift_fields)) != len(normalized_drift_fields):
            raise ValueError("Reconciliation drift fields must be unique.")

        object.__setattr__(
            self,
            "drift_fields",
            tuple(sorted(normalized_drift_fields)),
        )

        if self.outcome is BillingReconciliationOutcome.IN_SYNC:
            if self.drift_fields:
                raise ValueError("An in-sync reconciliation result must not contain drift fields.")

            if self.provider_state_version != self.previous_provider_state_version:
                raise ValueError(
                    "An in-sync reconciliation result must preserve the provider state version."
                )
            return

        if self.outcome is BillingReconciliationOutcome.REPAIRED:
            if not self.drift_fields:
                raise ValueError(
                    "A repaired reconciliation result must contain at least one drift field."
                )

            if self.provider_state_version < self.previous_provider_state_version:
                raise ValueError(
                    "A repaired reconciliation result cannot regress the provider state version."
                )
            return

        if (
            self.outcome is BillingReconciliationOutcome.IGNORED
            and self.provider_state_version >= self.previous_provider_state_version
        ):
            raise ValueError(
                "An ignored reconciliation result must reference an older provider snapshot."
            )


def _require_timezone_aware(
    value: datetime,
    *,
    field_name: str,
) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
