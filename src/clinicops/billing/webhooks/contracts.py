from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from clinicops.billing.enums import (
    BillingWebhookEventType,
    SubscriptionStatus,
)

WebhookIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
    ),
]
WebhookPriceCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
    ),
]
ProviderStateVersion = Annotated[
    int,
    Field(ge=1),
]


class BillingWebhookSubscriptionData(BaseModel):
    """Canonical provider subscription state carried by a webhook."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    provider_subscription_id: WebhookIdentifier
    provider_state_version: ProviderStateVersion
    price_code: WebhookPriceCode
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    canceled_at: datetime | None = None

    @field_validator(
        "current_period_start",
        "current_period_end",
        "canceled_at",
    )
    @classmethod
    def require_timezone_aware_datetimes(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Webhook timestamps must be timezone-aware.")

        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_valid_period(self) -> Self:
        if self.current_period_end <= self.current_period_start:
            raise ValueError("The webhook current period end must be after its start.")

        return self


class BillingWebhookEventEnvelope(BaseModel):
    """Canonical authenticated billing event envelope."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    provider_event_id: WebhookIdentifier = Field(
        alias="id",
    )
    event_type: BillingWebhookEventType = Field(
        alias="type",
    )
    created_at: datetime
    data: BillingWebhookSubscriptionData

    @field_validator("created_at")
    @classmethod
    def require_timezone_aware_created_at(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Webhook event timestamps must be timezone-aware.")

        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_event_lifecycle_consistency(
        self,
    ) -> Self:
        if self.event_type is BillingWebhookEventType.SUBSCRIPTION_RENEWED:
            if self.data.status is not SubscriptionStatus.ACTIVE:
                raise ValueError("A renewed subscription event must carry active status.")

            if self.data.canceled_at is not None:
                raise ValueError(
                    "A renewed subscription event cannot carry a cancellation timestamp."
                )

        if self.event_type is BillingWebhookEventType.SUBSCRIPTION_CANCELED:
            if self.data.status is not SubscriptionStatus.CANCELED:
                raise ValueError("A canceled subscription event must carry canceled status.")

            if self.data.canceled_at is None:
                raise ValueError(
                    "A canceled subscription event must carry a cancellation timestamp."
                )

        return self
