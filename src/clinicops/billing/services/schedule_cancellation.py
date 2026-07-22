from dataclasses import dataclass
from uuid import UUID

from clinicops.billing.services.get_subscription import (
    BillingSubscriptionDetails,
)


@dataclass(frozen=True, slots=True)
class ScheduleBillingSubscriptionCancellationCommand:
    """Schedule one tenant subscription cancellation at period end."""

    tenant_id: UUID
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ScheduledBillingSubscriptionCancellation(BillingSubscriptionDetails):
    """Persisted subscription state after scheduling cancellation."""

    replayed: bool
