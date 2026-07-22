from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    BillingWebhookProcessingOutcome,
    SubscriptionStatus,
)
from clinicops.billing.models import (
    BillingWebhookEvent,
    Subscription,
)
from clinicops.billing.webhooks.handlers import (
    BillingWebhookTerminalProcessingError,
    apply_billing_cancellation_event,
)

PERIOD_START = datetime(
    2026,
    7,
    22,
    12,
    tzinfo=UTC,
)
PERIOD_END = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
MID_PERIOD = datetime(
    2026,
    8,
    1,
    12,
    tzinfo=UTC,
)
REQUESTED_AT = datetime(
    2026,
    7,
    24,
    15,
    tzinfo=UTC,
)


def _subscription(
    *,
    provider_state_version: int = 4,
    status: SubscriptionStatus = (SubscriptionStatus.ACTIVE),
    cancel_at_period_end: bool = True,
    cancellation_requested_at: datetime | None = (REQUESTED_AT),
    canceled_at: datetime | None = None,
    pending_price_code: str | None = None,
) -> Subscription:
    return Subscription(
        id=uuid4(),
        tenant_id=uuid4(),
        billing_customer_id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_subscription_id="fake_sub_01",
        price_code="starter_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=4900,
        pending_price_code=pending_price_code,
        status=status,
        cancel_at_period_end=cancel_at_period_end,
        cancellation_requested_at=(cancellation_requested_at),
        current_period_start=PERIOD_START,
        current_period_end=PERIOD_END,
        provider_state_version=provider_state_version,
        last_provider_event_at=None,
        canceled_at=canceled_at,
    )


def _event(
    *,
    provider_state_version: int = 5,
    price_code: str = "starter_monthly",
    current_period_start: datetime = PERIOD_START,
    current_period_end: datetime = PERIOD_END,
    canceled_at: datetime = PERIOD_END,
) -> BillingWebhookEvent:
    provider_event_id = "evt_canceled_01"

    return BillingWebhookEvent(
        id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_CANCELED.value),
        provider_subscription_id="fake_sub_01",
        provider_created_at=canceled_at,
        provider_state_version=provider_state_version,
        payload={
            "id": provider_event_id,
            "type": "subscription.canceled",
            "created_at": canceled_at.isoformat(),
            "data": {
                "provider_subscription_id": "fake_sub_01",
                "provider_state_version": (provider_state_version),
                "price_code": price_code,
                "status": "canceled",
                "current_period_start": (current_period_start.isoformat()),
                "current_period_end": (current_period_end.isoformat()),
                "canceled_at": canceled_at.isoformat(),
            },
        },
        payload_sha256="b" * 64,
        signature_timestamp=int(canceled_at.timestamp()),
        status=BillingWebhookEventStatus.PROCESSING,
        processing_attempt_count=1,
    )


def test_scheduled_cancellation_is_finalized_at_period_end() -> None:
    subscription = _subscription(pending_price_code="professional_monthly")
    event = _event()

    result = apply_billing_cancellation_event(
        event=event,
        subscription=subscription,
    )

    assert result.subscription_id == subscription.id
    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert subscription.status is SubscriptionStatus.CANCELED
    assert subscription.canceled_at == PERIOD_END
    assert subscription.cancel_at_period_end is False
    assert subscription.cancellation_requested_at == (REQUESTED_AT)
    assert subscription.pending_price_code is None
    assert subscription.price_code == "starter_monthly"
    assert subscription.plan is BillingPlan.STARTER
    assert subscription.current_period_start == (PERIOD_START)
    assert subscription.current_period_end == PERIOD_END
    assert subscription.provider_state_version == 5
    assert subscription.last_provider_event_at == (PERIOD_END)


def test_provider_authoritative_mid_period_cancellation_is_applied() -> None:
    subscription = _subscription(
        cancel_at_period_end=False,
        cancellation_requested_at=None,
    )
    event = _event(canceled_at=MID_PERIOD)

    result = apply_billing_cancellation_event(
        event=event,
        subscription=subscription,
    )

    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert subscription.status is SubscriptionStatus.CANCELED
    assert subscription.canceled_at == MID_PERIOD
    assert subscription.cancellation_requested_at is None
    assert subscription.cancel_at_period_end is False


@pytest.mark.parametrize(
    "event_version",
    [
        3,
        4,
    ],
)
def test_stale_or_reflected_cancellation_is_ignored(
    event_version: int,
) -> None:
    subscription = _subscription(provider_state_version=4)
    original_status = subscription.status
    original_canceled_at = subscription.canceled_at

    result = apply_billing_cancellation_event(
        event=_event(provider_state_version=event_version),
        subscription=subscription,
    )

    assert result.outcome is BillingWebhookProcessingOutcome.IGNORED
    assert subscription.status is original_status
    assert subscription.canceled_at is original_canceled_at
    assert subscription.cancel_at_period_end is True
    assert subscription.provider_state_version == 4


def test_newer_consistent_final_cancellation_advances_version() -> None:
    subscription = _subscription(
        provider_state_version=5,
        status=SubscriptionStatus.CANCELED,
        cancel_at_period_end=False,
        canceled_at=PERIOD_END,
        pending_price_code=None,
    )

    result = apply_billing_cancellation_event(
        event=_event(provider_state_version=6),
        subscription=subscription,
    )

    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert subscription.provider_state_version == 6
    assert subscription.canceled_at == PERIOD_END
    assert subscription.status is SubscriptionStatus.CANCELED


@pytest.mark.parametrize(
    ("subscription", "event", "failure_code"),
    [
        (
            _subscription(),
            _event(canceled_at=MID_PERIOD),
            "cancellation_boundary_mismatch",
        ),
        (
            _subscription(
                cancel_at_period_end=False,
                cancellation_requested_at=None,
            ),
            _event(
                current_period_start=datetime(
                    2026,
                    8,
                    23,
                    12,
                    tzinfo=UTC,
                ),
                current_period_end=datetime(
                    2026,
                    9,
                    23,
                    12,
                    tzinfo=UTC,
                ),
                canceled_at=MID_PERIOD,
            ),
            "invalid_cancellation_timestamp",
        ),
        (
            _subscription(
                cancel_at_period_end=False,
                cancellation_requested_at=None,
            ),
            _event(
                current_period_start=datetime(
                    2026,
                    6,
                    22,
                    12,
                    tzinfo=UTC,
                ),
                current_period_end=datetime(
                    2026,
                    7,
                    22,
                    12,
                    tzinfo=UTC,
                ),
                canceled_at=datetime(
                    2026,
                    7,
                    1,
                    12,
                    tzinfo=UTC,
                ),
            ),
            "invalid_cancellation_period",
        ),
        (
            _subscription(),
            _event(price_code="professional_monthly"),
            "unexpected_price_code",
        ),
        (
            _subscription(
                provider_state_version=5,
                status=SubscriptionStatus.CANCELED,
                cancel_at_period_end=False,
                canceled_at=PERIOD_END,
                pending_price_code=None,
            ),
            _event(
                provider_state_version=6,
                canceled_at=MID_PERIOD,
            ),
            "inconsistent_final_cancellation",
        ),
    ],
)
def test_invalid_cancellation_transition_is_terminal(
    subscription: Subscription,
    event: BillingWebhookEvent,
    failure_code: str,
) -> None:
    with pytest.raises(BillingWebhookTerminalProcessingError) as exception_info:
        apply_billing_cancellation_event(
            event=event,
            subscription=subscription,
        )

    assert exception_info.value.failure_code == failure_code


def test_cancellation_rejects_persisted_metadata_mismatch() -> None:
    event = _event()
    event.provider_state_version = 6

    with pytest.raises(BillingWebhookTerminalProcessingError) as exception_info:
        apply_billing_cancellation_event(
            event=event,
            subscription=_subscription(),
        )

    assert exception_info.value.failure_code == "persisted_event_metadata_mismatch"
