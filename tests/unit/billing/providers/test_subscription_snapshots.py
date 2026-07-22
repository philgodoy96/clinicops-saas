from datetime import UTC, datetime
from uuid import UUID

import pytest

from clinicops.billing.enums import (
    BillingProvider,
    SubscriptionStatus,
)
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.providers.contracts import (
    CancelSubscriptionRequest,
    ChangePlanRequest,
    CreateCustomerRequest,
    CreateSubscriptionRequest,
)
from clinicops.billing.providers.fake import (
    FakePaymentProvider,
)
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
)

CREATE_CUSTOMER_KEY = build_provider_operation_key(UUID("96f33a49-1685-4ae6-aee4-fb5ebddaa94c"))
CREATE_SUBSCRIPTION_KEY = build_provider_operation_key(UUID("5fed4e42-5f15-4f7c-a20c-c79fa49f88a7"))
CHANGE_PLAN_KEY = build_provider_operation_key(UUID("6d31f2aa-0a43-4655-991a-f81777222db7"))
CANCEL_SUBSCRIPTION_KEY = build_provider_operation_key(UUID("06c2d33e-d774-4a9f-b0ef-f59ebf40a3ba"))
PERIOD_START = datetime(
    2026,
    7,
    21,
    12,
    tzinfo=UTC,
)
BEFORE_PERIOD_END = datetime(
    2026,
    8,
    20,
    12,
    tzinfo=UTC,
)
MONTHLY_PERIOD_END = datetime(
    2026,
    8,
    21,
    12,
    tzinfo=UTC,
)
YEARLY_PERIOD_END = datetime(
    2027,
    8,
    21,
    12,
    tzinfo=UTC,
)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


def _create_subscription(
    provider: PaymentProvider,
) -> str:
    customer = provider.create_customer(
        CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)
    )
    subscription = provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=(CREATE_SUBSCRIPTION_KEY),
            provider_customer_id=(customer.provider_customer_id),
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )

    return subscription.provider_subscription_id


def test_fake_provider_snapshot_satisfies_protocol() -> None:
    provider: PaymentProvider = FakePaymentProvider(clock=MutableClock(BEFORE_PERIOD_END))
    provider_subscription_id = _create_subscription(provider)

    snapshot = provider.get_subscription_snapshot(provider_subscription_id)

    assert snapshot is not None
    assert snapshot.provider is BillingProvider.FAKE


def test_snapshot_returns_none_for_unknown_subscription() -> None:
    provider = FakePaymentProvider(clock=MutableClock(BEFORE_PERIOD_END))

    assert provider.get_subscription_snapshot("fake_sub_missing") is None


def test_snapshot_rejects_empty_subscription_id() -> None:
    provider = FakePaymentProvider(clock=MutableClock(BEFORE_PERIOD_END))

    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        provider.get_subscription_snapshot(" ")


def test_created_subscription_returns_active_snapshot() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_subscription_id = _create_subscription(provider)

    snapshot = provider.get_subscription_snapshot(provider_subscription_id)

    assert snapshot is not None
    assert snapshot.provider is BillingProvider.FAKE
    assert snapshot.provider_subscription_id == provider_subscription_id
    assert snapshot.provider_state_version == 1
    assert snapshot.price_code == "starter_monthly"
    assert snapshot.status is SubscriptionStatus.ACTIVE
    assert snapshot.current_period_start == PERIOD_START
    assert snapshot.current_period_end == MONTHLY_PERIOD_END
    assert snapshot.cancel_at_period_end is False
    assert snapshot.canceled_at is None
    assert snapshot.observed_at == BEFORE_PERIOD_END


def test_pending_plan_snapshot_preserves_active_period_before_boundary() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_subscription_id = _create_subscription(provider)

    changed = provider.change_plan(
        ChangePlanRequest(
            provider_operation_key=CHANGE_PLAN_KEY,
            provider_subscription_id=(provider_subscription_id),
            target_price_code="professional_yearly",
            effective_at=MONTHLY_PERIOD_END,
        )
    )
    snapshot = provider.get_subscription_snapshot(provider_subscription_id)

    assert changed.provider_state_version == 2
    assert snapshot is not None
    assert snapshot.provider_state_version == 2
    assert snapshot.price_code == "starter_monthly"
    assert snapshot.status is SubscriptionStatus.ACTIVE
    assert snapshot.current_period_start == PERIOD_START
    assert snapshot.current_period_end == MONTHLY_PERIOD_END


def test_plan_change_materializes_when_boundary_is_observed() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_subscription_id = _create_subscription(provider)

    provider.change_plan(
        ChangePlanRequest(
            provider_operation_key=CHANGE_PLAN_KEY,
            provider_subscription_id=(provider_subscription_id),
            target_price_code="professional_yearly",
            effective_at=MONTHLY_PERIOD_END,
        )
    )
    clock.current = MONTHLY_PERIOD_END

    snapshot = provider.get_subscription_snapshot(provider_subscription_id)
    replayed = provider.get_subscription_snapshot(provider_subscription_id)

    assert snapshot is not None
    assert snapshot.provider_state_version == 3
    assert snapshot.price_code == "professional_yearly"
    assert snapshot.current_period_start == MONTHLY_PERIOD_END
    assert snapshot.current_period_end == (YEARLY_PERIOD_END)
    assert snapshot.cancel_at_period_end is False
    assert snapshot.canceled_at is None
    assert replayed == snapshot


def test_pending_cancellation_snapshot_is_active_before_boundary() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_subscription_id = _create_subscription(provider)

    canceled = provider.cancel_subscription(
        CancelSubscriptionRequest(
            provider_operation_key=(CANCEL_SUBSCRIPTION_KEY),
            provider_subscription_id=(provider_subscription_id),
            effective_at=MONTHLY_PERIOD_END,
        )
    )
    snapshot = provider.get_subscription_snapshot(provider_subscription_id)

    assert canceled.provider_state_version == 2
    assert snapshot is not None
    assert snapshot.provider_state_version == 2
    assert snapshot.status is SubscriptionStatus.ACTIVE
    assert snapshot.cancel_at_period_end is True
    assert snapshot.canceled_at is None
    assert snapshot.current_period_end == (MONTHLY_PERIOD_END)


def test_cancellation_materializes_when_boundary_is_observed() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_subscription_id = _create_subscription(provider)

    provider.cancel_subscription(
        CancelSubscriptionRequest(
            provider_operation_key=(CANCEL_SUBSCRIPTION_KEY),
            provider_subscription_id=(provider_subscription_id),
            effective_at=MONTHLY_PERIOD_END,
        )
    )
    clock.current = MONTHLY_PERIOD_END

    snapshot = provider.get_subscription_snapshot(provider_subscription_id)
    replayed = provider.get_subscription_snapshot(provider_subscription_id)

    assert snapshot is not None
    assert snapshot.provider_state_version == 3
    assert snapshot.status is SubscriptionStatus.CANCELED
    assert snapshot.cancel_at_period_end is False
    assert snapshot.canceled_at == MONTHLY_PERIOD_END
    assert replayed == snapshot


def test_returned_snapshot_is_detached_from_provider_state() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_subscription_id = _create_subscription(provider)
    initial = provider.get_subscription_snapshot(provider_subscription_id)

    provider.cancel_subscription(
        CancelSubscriptionRequest(
            provider_operation_key=(CANCEL_SUBSCRIPTION_KEY),
            provider_subscription_id=(provider_subscription_id),
            effective_at=MONTHLY_PERIOD_END,
        )
    )
    clock.current = MONTHLY_PERIOD_END
    final = provider.get_subscription_snapshot(provider_subscription_id)

    assert initial is not None
    assert final is not None
    assert initial.provider_state_version == 1
    assert initial.status is SubscriptionStatus.ACTIVE
    assert initial.cancel_at_period_end is False
    assert initial.canceled_at is None

    assert final.provider_state_version == 3
    assert final.status is SubscriptionStatus.CANCELED
    assert final.canceled_at == MONTHLY_PERIOD_END
