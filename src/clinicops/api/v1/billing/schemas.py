from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
)

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)

PriceCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
    ),
]


class CreateBillingSubscriptionRequest(BaseModel):
    """Request body for tenant billing subscription creation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    price_code: PriceCode


class ScheduleBillingPlanChangeRequest(CreateBillingSubscriptionRequest):
    """Request one scheduled subscription price change."""


class BillingSubscriptionResponse(BaseModel):
    """Public representation of a tenant billing subscription."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
    )

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
