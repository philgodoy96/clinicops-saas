from dataclasses import dataclass
from uuid import UUID

from clinicops.billing.services.get_subscription import (
    BillingSubscriptionDetails,
)


@dataclass(frozen=True, slots=True)
class ScheduleBillingPlanChangeCommand:
    """Schedule one tenant subscription price change."""

    tenant_id: UUID
    target_price_code: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ScheduledBillingPlanChange(BillingSubscriptionDetails):
    """Persisted subscription state after scheduling a plan change."""

    replayed: bool
