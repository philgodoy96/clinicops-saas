from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from clinicops.billing.catalog import get_price_definition
from clinicops.billing.enums import (
    BillingWebhookEventType,
    BillingWebhookProcessingOutcome,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    UnsupportedPriceCodeError,
)
from clinicops.billing.models import (
    BillingWebhookEvent,
    Subscription,
)
from clinicops.billing.webhooks.contracts import (
    BillingWebhookEventEnvelope,
)


@dataclass(frozen=True, slots=True)
class BillingWebhookHandlerResult:
    """Successful event-specific subscription transition."""

    subscription_id: UUID
    outcome: BillingWebhookProcessingOutcome


class BillingWebhookTerminalProcessingError(Exception):
    """Internal terminal failure classified for durable persistence."""

    def __init__(
        self,
        *,
        failure_code: str,
        internal_message: str,
    ) -> None:
        normalized_failure_code = failure_code.strip()
        normalized_message = internal_message.strip()

        if not normalized_failure_code:
            raise ValueError("The webhook processing failure code must not be empty.")

        if not normalized_message:
            raise ValueError("The webhook processing failure message must not be empty.")

        self.failure_code = normalized_failure_code
        self.internal_message = normalized_message

        super().__init__(normalized_message)


def apply_billing_renewal_event(
    *,
    event: BillingWebhookEvent,
    subscription: Subscription,
) -> BillingWebhookHandlerResult:
    """Apply a monotonic renewal transition to one locked subscription."""

    envelope = _parse_persisted_event(event)
    _validate_persisted_event_identity(
        event=event,
        envelope=envelope,
    )
    _validate_subscription_identity(
        event=event,
        subscription=subscription,
    )

    if event.provider_state_version <= subscription.provider_state_version:
        return BillingWebhookHandlerResult(
            subscription_id=subscription.id,
            outcome=BillingWebhookProcessingOutcome.IGNORED,
        )

    if envelope.event_type is not BillingWebhookEventType.SUBSCRIPTION_RENEWED:
        raise BillingWebhookTerminalProcessingError(
            failure_code="unsupported_event_type",
            internal_message=("The renewal handler received a non-renewal event."),
        )

    if subscription.status is SubscriptionStatus.CANCELED or subscription.canceled_at is not None:
        raise BillingWebhookTerminalProcessingError(
            failure_code="subscription_already_canceled",
            internal_message=("A renewal event cannot reactivate a canceled subscription."),
        )

    if subscription.cancel_at_period_end:
        raise BillingWebhookTerminalProcessingError(
            failure_code="cancellation_pending",
            internal_message=("A renewal event cannot implicitly reverse a pending cancellation."),
        )

    if subscription.status not in {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
    }:
        raise BillingWebhookTerminalProcessingError(
            failure_code="incompatible_subscription_status",
            internal_message=("The local subscription status is not eligible for renewal."),
        )

    current_period_end = subscription.current_period_end

    if subscription.current_period_start is None or current_period_end is None:
        raise BillingWebhookTerminalProcessingError(
            failure_code="missing_local_billing_period",
            internal_message=("The local subscription does not have a complete billing period."),
        )

    if envelope.data.current_period_start != current_period_end:
        raise BillingWebhookTerminalProcessingError(
            failure_code="invalid_period_transition",
            internal_message=(
                "The renewal period does not start at the current local period boundary."
            ),
        )

    expected_price_code = (
        subscription.pending_price_code
        if subscription.pending_price_code is not None
        else subscription.price_code
    )

    if envelope.data.price_code != expected_price_code:
        failure_code = (
            "pending_price_code_mismatch"
            if subscription.pending_price_code is not None
            else "unexpected_price_code"
        )
        raise BillingWebhookTerminalProcessingError(
            failure_code=failure_code,
            internal_message=(
                "The renewal event price code does not match the expected local price code."
            ),
        )

    try:
        price = get_price_definition(envelope.data.price_code)
    except UnsupportedPriceCodeError as error:
        raise BillingWebhookTerminalProcessingError(
            failure_code="unsupported_price_code",
            internal_message=("The renewal event references an unsupported ClinicOps price code."),
        ) from error

    subscription.price_code = price.price_code
    subscription.plan = price.plan
    subscription.billing_interval = price.billing_interval
    subscription.currency = price.currency
    subscription.unit_amount = price.unit_amount
    subscription.pending_price_code = None
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.current_period_start = envelope.data.current_period_start
    subscription.current_period_end = envelope.data.current_period_end
    subscription.provider_state_version = event.provider_state_version
    subscription.last_provider_event_at = event.provider_created_at

    return BillingWebhookHandlerResult(
        subscription_id=subscription.id,
        outcome=BillingWebhookProcessingOutcome.APPLIED,
    )


def _parse_persisted_event(
    event: BillingWebhookEvent,
) -> BillingWebhookEventEnvelope:
    try:
        return BillingWebhookEventEnvelope.model_validate(event.payload)
    except ValidationError as error:
        raise BillingWebhookTerminalProcessingError(
            failure_code="invalid_persisted_payload",
            internal_message=(
                "The persisted webhook payload is no longer a valid canonical billing event."
            ),
        ) from error


def _validate_persisted_event_identity(
    *,
    event: BillingWebhookEvent,
    envelope: BillingWebhookEventEnvelope,
) -> None:
    if (
        envelope.provider_event_id != event.provider_event_id
        or envelope.event_type.value != event.event_type
        or envelope.created_at != event.provider_created_at
        or envelope.data.provider_subscription_id != event.provider_subscription_id
        or envelope.data.provider_state_version != event.provider_state_version
    ):
        raise BillingWebhookTerminalProcessingError(
            failure_code="persisted_event_metadata_mismatch",
            internal_message=(
                "The persisted webhook columns do not match the canonical event payload."
            ),
        )


def _validate_subscription_identity(
    *,
    event: BillingWebhookEvent,
    subscription: Subscription,
) -> None:
    if (
        subscription.provider is not event.provider
        or subscription.provider_subscription_id != event.provider_subscription_id
    ):
        raise BillingWebhookTerminalProcessingError(
            failure_code="provider_subscription_mismatch",
            internal_message=(
                "The resolved local subscription does not match the provider event identity."
            ),
        )
