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
    apply_billing_renewal_event,
)

CURRENT_PERIOD_START = datetime(
    2026,
    7,
    22,
    12,
    tzinfo=UTC,
)
CURRENT_PERIOD_END = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
NEXT_PERIOD_END = datetime(
    2026,
    9,
    22,
    12,
    tzinfo=UTC,
)


def _subscription(
    *,
    provider_state_version: int = 3,
    pending_price_code: str | None = ("professional_monthly"),
    status: SubscriptionStatus = (SubscriptionStatus.ACTIVE),
    cancel_at_period_end: bool = False,
    canceled_at: datetime | None = None,
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
        cancellation_requested_at=(CURRENT_PERIOD_START if cancel_at_period_end else None),
        current_period_start=CURRENT_PERIOD_START,
        current_period_end=CURRENT_PERIOD_END,
        provider_state_version=provider_state_version,
        last_provider_event_at=None,
        canceled_at=canceled_at,
    )


def _event(
    *,
    provider_state_version: int = 4,
    price_code: str = "professional_monthly",
    current_period_start: datetime = (CURRENT_PERIOD_END),
    current_period_end: datetime = NEXT_PERIOD_END,
) -> BillingWebhookEvent:
    provider_event_id = "evt_renewed_01"
    payload = {
        "id": provider_event_id,
        "type": "subscription.renewed",
        "created_at": (CURRENT_PERIOD_END.isoformat()),
        "data": {
            "provider_subscription_id": "fake_sub_01",
            "provider_state_version": (provider_state_version),
            "price_code": price_code,
            "status": "active",
            "current_period_start": (current_period_start.isoformat()),
            "current_period_end": (current_period_end.isoformat()),
            "canceled_at": None,
        },
    }

    return BillingWebhookEvent(
        id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
        provider_subscription_id="fake_sub_01",
        provider_created_at=CURRENT_PERIOD_END,
        provider_state_version=provider_state_version,
        payload=payload,
        payload_sha256="a" * 64,
        signature_timestamp=int(CURRENT_PERIOD_END.timestamp()),
        status=BillingWebhookEventStatus.PROCESSING,
        processing_attempt_count=1,
    )


def test_renewal_applies_pending_plan_from_local_catalog() -> None:
    subscription = _subscription()
    event = _event()

    result = apply_billing_renewal_event(
        event=event,
        subscription=subscription,
    )

    assert result.subscription_id == subscription.id
    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert subscription.price_code == ("professional_monthly")
    assert subscription.plan is BillingPlan.PROFESSIONAL
    assert subscription.billing_interval is BillingInterval.MONTHLY
    assert subscription.currency == "USD"
    assert subscription.unit_amount == 9900
    assert subscription.pending_price_code is None
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.current_period_start == CURRENT_PERIOD_END
    assert subscription.current_period_end == NEXT_PERIOD_END
    assert subscription.provider_state_version == 4
    assert subscription.last_provider_event_at == CURRENT_PERIOD_END


@pytest.mark.parametrize(
    "event_version",
    [
        2,
        3,
    ],
)
def test_stale_or_reflected_renewal_is_ignored(
    event_version: int,
) -> None:
    subscription = _subscription(provider_state_version=3)
    original_price_code = subscription.price_code
    original_period_start = subscription.current_period_start
    original_period_end = subscription.current_period_end

    result = apply_billing_renewal_event(
        event=_event(provider_state_version=event_version),
        subscription=subscription,
    )

    assert result.outcome is BillingWebhookProcessingOutcome.IGNORED
    assert subscription.price_code == original_price_code
    assert subscription.current_period_start == original_period_start
    assert subscription.current_period_end == original_period_end
    assert subscription.provider_state_version == 3
    assert subscription.pending_price_code == ("professional_monthly")


def test_regular_renewal_preserves_current_price() -> None:
    subscription = _subscription(pending_price_code=None)

    result = apply_billing_renewal_event(
        event=_event(price_code="starter_monthly"),
        subscription=subscription,
    )

    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert subscription.price_code == "starter_monthly"
    assert subscription.unit_amount == 4900


@pytest.mark.parametrize(
    ("subscription", "event", "failure_code"),
    [
        (
            _subscription(pending_price_code=("professional_monthly")),
            _event(price_code="starter_yearly"),
            "pending_price_code_mismatch",
        ),
        (
            _subscription(pending_price_code=None),
            _event(price_code="professional_monthly"),
            "unexpected_price_code",
        ),
        (
            _subscription(),
            _event(
                current_period_start=datetime(
                    2026,
                    8,
                    23,
                    12,
                    tzinfo=UTC,
                )
            ),
            "invalid_period_transition",
        ),
        (
            _subscription(cancel_at_period_end=True),
            _event(),
            "cancellation_pending",
        ),
        (
            _subscription(
                status=SubscriptionStatus.CANCELED,
                pending_price_code=None,
                canceled_at=CURRENT_PERIOD_END,
            ),
            _event(price_code="starter_monthly"),
            "subscription_already_canceled",
        ),
        (
            _subscription(status=SubscriptionStatus.PENDING),
            _event(),
            "incompatible_subscription_status",
        ),
    ],
)
def test_invalid_renewal_transition_is_terminal(
    subscription: Subscription,
    event: BillingWebhookEvent,
    failure_code: str,
) -> None:
    with pytest.raises(BillingWebhookTerminalProcessingError) as exception_info:
        apply_billing_renewal_event(
            event=event,
            subscription=subscription,
        )

    assert exception_info.value.failure_code == failure_code


def test_renewal_rejects_unsupported_price_code() -> None:
    subscription = _subscription(pending_price_code="unknown_price")

    with pytest.raises(BillingWebhookTerminalProcessingError) as exception_info:
        apply_billing_renewal_event(
            event=_event(price_code="unknown_price"),
            subscription=subscription,
        )

    assert exception_info.value.failure_code == "unsupported_price_code"


def test_renewal_rejects_persisted_metadata_mismatch() -> None:
    event = _event()
    event.provider_state_version = 5

    with pytest.raises(BillingWebhookTerminalProcessingError) as exception_info:
        apply_billing_renewal_event(
            event=event,
            subscription=_subscription(),
        )

    assert exception_info.value.failure_code == "persisted_event_metadata_mismatch"
