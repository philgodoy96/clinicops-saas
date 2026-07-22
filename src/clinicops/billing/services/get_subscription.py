from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingSubscriptionNotFoundError,
)
from clinicops.billing.models import Subscription
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
)


@dataclass(frozen=True, slots=True)
class GetBillingSubscriptionQuery:
    """Tenant-scoped query for the current billing subscription."""

    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class BillingSubscriptionDetails:
    """Provider-agnostic public details for a tenant subscription."""

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


class GetBillingSubscriptionService:
    """Return the current persisted subscription for one tenant."""

    def __init__(
        self,
        subscription_repository: SubscriptionRepository | None = None,
    ) -> None:
        self._subscription_repository = (
            subscription_repository
            if subscription_repository is not None
            else SubscriptionRepository()
        )

    def execute(
        self,
        session: Session,
        query: GetBillingSubscriptionQuery,
    ) -> BillingSubscriptionDetails:
        """Read the tenant subscription without mutating persistence."""

        subscription = self._subscription_repository.get_by_tenant_id(
            session,
            tenant_id=query.tenant_id,
        )

        if subscription is None:
            raise BillingSubscriptionNotFoundError()

        return _to_details(subscription)


def _to_details(
    subscription: Subscription,
) -> BillingSubscriptionDetails:
    return BillingSubscriptionDetails(
        id=subscription.id,
        tenant_id=subscription.tenant_id,
        price_code=subscription.price_code,
        plan=subscription.plan,
        billing_interval=subscription.billing_interval,
        currency=subscription.currency,
        unit_amount=subscription.unit_amount,
        status=subscription.status,
        current_period_start=_require_datetime(
            subscription.current_period_start,
            field_name="current_period_start",
        ),
        current_period_end=_require_datetime(
            subscription.current_period_end,
            field_name="current_period_end",
        ),
        cancel_at_period_end=(subscription.cancel_at_period_end),
        cancellation_requested_at=(subscription.cancellation_requested_at),
        canceled_at=subscription.canceled_at,
        pending_price_code=subscription.pending_price_code,
        created_at=_require_datetime(
            subscription.created_at,
            field_name="created_at",
        ),
        updated_at=_require_datetime(
            subscription.updated_at,
            field_name="updated_at",
        ),
    )


def _require_datetime(
    value: datetime | None,
    *,
    field_name: str,
) -> datetime:
    if value is None:
        raise RuntimeError(f"{field_name} is required for a persisted billing subscription.")

    return value
