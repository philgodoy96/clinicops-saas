from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingError,
    BillingSubscriptionAlreadyCanceledError,
    BillingSubscriptionCancellationPendingError,
    BillingSubscriptionNotActiveError,
)
from clinicops.billing.services.schedule_cancellation import (
    ScheduleBillingSubscriptionCancellationCommand,
    ScheduledBillingSubscriptionCancellation,
)

TENANT_ID = UUID("5c9d9f0b-bffd-4519-85e4-817f365daee8")
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
CANCELLATION_REQUESTED_AT = datetime(
    2026,
    7,
    24,
    15,
    tzinfo=UTC,
)


def test_schedule_cancellation_command_is_immutable() -> None:
    command = ScheduleBillingSubscriptionCancellationCommand(
        tenant_id=TENANT_ID,
        idempotency_key="cancellation-request-1",
    )

    with pytest.raises(FrozenInstanceError):
        command.idempotency_key = (  # type: ignore[misc]
            "another-cancellation-request"
        )


def test_scheduled_cancellation_exposes_public_subscription_state() -> None:
    result = ScheduledBillingSubscriptionCancellation(
        id=uuid4(),
        tenant_id=TENANT_ID,
        price_code="starter_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=4900,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=PERIOD_START,
        current_period_end=PERIOD_END,
        cancel_at_period_end=True,
        cancellation_requested_at=(CANCELLATION_REQUESTED_AT),
        canceled_at=None,
        pending_price_code=None,
        created_at=PERIOD_START,
        updated_at=CANCELLATION_REQUESTED_AT,
        replayed=False,
    )

    assert result.status is SubscriptionStatus.ACTIVE
    assert result.cancel_at_period_end is True
    assert result.cancellation_requested_at == CANCELLATION_REQUESTED_AT
    assert result.canceled_at is None
    assert result.pending_price_code is None
    assert result.replayed is False
    assert not hasattr(
        result,
        "provider_subscription_id",
    )
    assert not hasattr(
        result,
        "provider_reference",
    )


@pytest.mark.parametrize(
    ("error_type", "expected_code"),
    [
        (
            BillingSubscriptionCancellationPendingError,
            "billing_subscription_cancellation_pending",
        ),
        (
            BillingSubscriptionNotActiveError,
            "billing_subscription_not_active",
        ),
        (
            BillingSubscriptionAlreadyCanceledError,
            "billing_subscription_already_canceled",
        ),
    ],
)
def test_cancellation_errors_have_stable_codes(
    error_type: type[BillingError],
    expected_code: str,
) -> None:
    error = error_type()

    assert error.code == expected_code
    assert isinstance(
        error.public_message,
        str,
    )
