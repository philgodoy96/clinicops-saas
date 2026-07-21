from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from clinicops.billing.enums import SubscriptionStatus
from clinicops.billing.exceptions import (
    BillingError,
    BillingPeriodBoundaryNotReachedError,
    CanceledSubscriptionMutationError,
    CancellationAlreadyRequestedError,
    CancellationNotRequestedError,
    InvalidSubscriptionTransitionError,
    PendingPlanChangeError,
    PendingPlanChangeNotFoundError,
    PlanAlreadySelectedError,
    SubscriptionMutationNotAllowedError,
)
from clinicops.billing.state_machine import (
    SubscriptionLifecycleState,
    confirm_scheduled_price_change,
    finalize_period_end_cancellation,
    is_stale_provider_state_version,
    request_period_end_cancellation,
    schedule_price_change,
    validate_subscription_transition,
)

PERIOD_END = datetime(2026, 8, 1, tzinfo=UTC)
NEXT_PERIOD_END = datetime(2026, 9, 1, tzinfo=UTC)
REQUESTED_AT = datetime(2026, 7, 15, tzinfo=UTC)


def _state(
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    price_code: str = "starter_monthly",
    pending_price_code: str | None = None,
    cancel_at_period_end: bool = False,
    cancellation_requested_at: datetime | None = None,
    canceled_at: datetime | None = None,
) -> SubscriptionLifecycleState:
    return SubscriptionLifecycleState(
        status=status,
        price_code=price_code,
        current_period_end=PERIOD_END,
        pending_price_code=pending_price_code,
        cancel_at_period_end=cancel_at_period_end,
        cancellation_requested_at=cancellation_requested_at,
        canceled_at=canceled_at,
    )


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        (
            SubscriptionStatus.PENDING,
            SubscriptionStatus.ACTIVE,
        ),
        (
            SubscriptionStatus.PENDING,
            SubscriptionStatus.CANCELED,
        ),
        (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PAST_DUE,
        ),
        (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.CANCELED,
        ),
        (
            SubscriptionStatus.PAST_DUE,
            SubscriptionStatus.ACTIVE,
        ),
        (
            SubscriptionStatus.PAST_DUE,
            SubscriptionStatus.CANCELED,
        ),
    ],
)
def test_validate_subscription_transition_accepts_supported_changes(
    current_status: SubscriptionStatus,
    target_status: SubscriptionStatus,
) -> None:
    validate_subscription_transition(
        current_status,
        target_status,
    )


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PENDING,
        ),
        (
            SubscriptionStatus.PAST_DUE,
            SubscriptionStatus.PENDING,
        ),
        (
            SubscriptionStatus.CANCELED,
            SubscriptionStatus.ACTIVE,
        ),
        (
            SubscriptionStatus.CANCELED,
            SubscriptionStatus.PAST_DUE,
        ),
    ],
)
def test_validate_subscription_transition_rejects_invalid_changes(
    current_status: SubscriptionStatus,
    target_status: SubscriptionStatus,
) -> None:
    with pytest.raises(InvalidSubscriptionTransitionError) as exception_info:
        validate_subscription_transition(
            current_status,
            target_status,
        )

    error = exception_info.value

    assert error.current_status is current_status
    assert error.target_status is target_status


def test_validate_subscription_transition_accepts_same_status() -> None:
    validate_subscription_transition(
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.ACTIVE,
    )


@pytest.mark.parametrize(
    (
        "current_version",
        "incoming_version",
        "expected_stale",
    ),
    [
        (0, 0, False),
        (4, 4, False),
        (4, 5, False),
        (5, 4, True),
    ],
)
def test_provider_state_version_staleness(
    current_version: int,
    incoming_version: int,
    expected_stale: bool,
) -> None:
    assert (
        is_stale_provider_state_version(
            current_version=current_version,
            incoming_version=incoming_version,
        )
        is expected_stale
    )


@pytest.mark.parametrize(
    ("current_version", "incoming_version"),
    [
        (-1, 0),
        (0, -1),
    ],
)
def test_provider_state_version_rejects_negative_values(
    current_version: int,
    incoming_version: int,
) -> None:
    with pytest.raises(ValueError):
        is_stale_provider_state_version(
            current_version=current_version,
            incoming_version=incoming_version,
        )


def test_schedule_price_change_reserves_target_price() -> None:
    original = _state()

    updated = schedule_price_change(
        original,
        target_price_code="professional_monthly",
    )

    assert original.pending_price_code is None
    assert updated.pending_price_code == "professional_monthly"
    assert updated.price_code == "starter_monthly"


def test_schedule_price_change_rejects_active_price() -> None:
    with pytest.raises(PlanAlreadySelectedError):
        schedule_price_change(
            _state(),
            target_price_code="starter_monthly",
        )


def test_schedule_price_change_rejects_an_existing_pending_change() -> None:
    with pytest.raises(PendingPlanChangeError) as exception_info:
        schedule_price_change(
            _state(pending_price_code="professional_monthly"),
            target_price_code="starter_yearly",
        )

    assert exception_info.value.pending_price_code == "professional_monthly"


def test_schedule_price_change_rejects_scheduled_cancellation() -> None:
    with pytest.raises(CancellationAlreadyRequestedError):
        schedule_price_change(
            _state(
                cancel_at_period_end=True,
                cancellation_requested_at=REQUESTED_AT,
            ),
            target_price_code="professional_monthly",
        )


def test_schedule_price_change_rejects_canceled_subscription() -> None:
    with pytest.raises(CanceledSubscriptionMutationError):
        schedule_price_change(
            _state(
                status=SubscriptionStatus.CANCELED,
                canceled_at=PERIOD_END,
            ),
            target_price_code="professional_monthly",
        )


def test_schedule_price_change_rejects_pending_subscription() -> None:
    with pytest.raises(SubscriptionMutationNotAllowedError):
        schedule_price_change(
            _state(status=SubscriptionStatus.PENDING),
            target_price_code="professional_monthly",
        )


def test_cancellation_clears_pending_price_change() -> None:
    original = _state(pending_price_code="professional_monthly")

    updated = request_period_end_cancellation(
        original,
        requested_at=REQUESTED_AT,
    )

    assert updated.pending_price_code is None
    assert updated.cancel_at_period_end is True
    assert updated.cancellation_requested_at == REQUESTED_AT
    assert updated.status is SubscriptionStatus.ACTIVE


def test_cancellation_rejects_duplicate_request() -> None:
    with pytest.raises(CancellationAlreadyRequestedError):
        request_period_end_cancellation(
            _state(
                cancel_at_period_end=True,
                cancellation_requested_at=REQUESTED_AT,
            ),
            requested_at=REQUESTED_AT,
        )


def test_cancellation_rejects_canceled_subscription() -> None:
    with pytest.raises(CanceledSubscriptionMutationError):
        request_period_end_cancellation(
            _state(
                status=SubscriptionStatus.CANCELED,
                canceled_at=PERIOD_END,
            ),
            requested_at=REQUESTED_AT,
        )


def test_confirm_price_change_applies_at_period_boundary() -> None:
    state = _state(pending_price_code="professional_monthly")

    updated = confirm_scheduled_price_change(
        state,
        confirmed_at=PERIOD_END,
        next_period_end=NEXT_PERIOD_END,
    )

    assert updated.price_code == "professional_monthly"
    assert updated.pending_price_code is None
    assert updated.current_period_end == NEXT_PERIOD_END


def test_confirm_price_change_rejects_early_confirmation() -> None:
    state = _state(pending_price_code="professional_monthly")

    with pytest.raises(BillingPeriodBoundaryNotReachedError):
        confirm_scheduled_price_change(
            state,
            confirmed_at=REQUESTED_AT,
            next_period_end=NEXT_PERIOD_END,
        )


def test_confirm_price_change_requires_pending_change() -> None:
    with pytest.raises(PendingPlanChangeNotFoundError):
        confirm_scheduled_price_change(
            _state(),
            confirmed_at=PERIOD_END,
            next_period_end=NEXT_PERIOD_END,
        )


def test_confirm_price_change_requires_future_period_end() -> None:
    state = _state(pending_price_code="professional_monthly")

    with pytest.raises(ValueError):
        confirm_scheduled_price_change(
            state,
            confirmed_at=PERIOD_END,
            next_period_end=PERIOD_END,
        )


def test_finalize_cancellation_applies_at_period_boundary() -> None:
    state = _state(
        pending_price_code="professional_monthly",
        cancel_at_period_end=True,
        cancellation_requested_at=REQUESTED_AT,
    )

    updated = finalize_period_end_cancellation(
        state,
        canceled_at=PERIOD_END,
    )

    assert updated.status is SubscriptionStatus.CANCELED
    assert updated.pending_price_code is None
    assert updated.cancel_at_period_end is False
    assert updated.canceled_at == PERIOD_END
    assert updated.cancellation_requested_at == REQUESTED_AT


def test_finalize_cancellation_rejects_early_confirmation() -> None:
    state = _state(
        cancel_at_period_end=True,
        cancellation_requested_at=REQUESTED_AT,
    )

    with pytest.raises(BillingPeriodBoundaryNotReachedError):
        finalize_period_end_cancellation(
            state,
            canceled_at=REQUESTED_AT,
        )


def test_finalize_cancellation_requires_prior_request() -> None:
    with pytest.raises(CancellationNotRequestedError):
        finalize_period_end_cancellation(
            _state(),
            canceled_at=PERIOD_END,
        )


def test_finalize_cancellation_is_idempotent_for_canceled_state() -> None:
    state = _state(
        status=SubscriptionStatus.CANCELED,
        canceled_at=PERIOD_END,
    )

    updated = finalize_period_end_cancellation(
        state,
        canceled_at=PERIOD_END,
    )

    assert updated is state


def test_lifecycle_state_is_immutable() -> None:
    state = _state()

    with pytest.raises(FrozenInstanceError):
        state.price_code = "professional_monthly"  # type: ignore[misc]


def test_lifecycle_state_requires_timezone_aware_period_end() -> None:
    with pytest.raises(
        ValueError,
        match="current_period_end must be timezone-aware",
    ):
        SubscriptionLifecycleState(
            status=SubscriptionStatus.ACTIVE,
            price_code="starter_monthly",
            current_period_end=datetime(2026, 8, 1),
        )


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_message"),
    [
        (
            InvalidSubscriptionTransitionError(
                SubscriptionStatus.CANCELED,
                SubscriptionStatus.ACTIVE,
            ),
            "invalid_subscription_transition",
            ("The requested subscription status transition is not permitted."),
        ),
        (
            SubscriptionMutationNotAllowedError(
                SubscriptionStatus.PENDING,
                "schedule_price_change",
            ),
            "subscription_mutation_not_allowed",
            ("The subscription cannot be modified in its current state."),
        ),
        (
            PlanAlreadySelectedError("starter_monthly"),
            "plan_already_selected",
            "The selected billing price is already active.",
        ),
        (
            PendingPlanChangeError("professional_monthly"),
            "pending_plan_change",
            "A billing price change is already pending.",
        ),
        (
            CancellationAlreadyRequestedError(),
            "cancellation_already_requested",
            "Subscription cancellation is already scheduled.",
        ),
        (
            CancellationNotRequestedError(),
            "cancellation_not_requested",
            "Subscription cancellation has not been scheduled.",
        ),
        (
            CanceledSubscriptionMutationError(),
            "canceled_subscription_mutation",
            "A canceled subscription cannot be modified.",
        ),
        (
            PendingPlanChangeNotFoundError(),
            "pending_plan_change_not_found",
            "No billing price change is currently pending.",
        ),
        (
            BillingPeriodBoundaryNotReachedError(),
            "billing_period_boundary_not_reached",
            "The current billing period has not ended yet.",
        ),
    ],
)
def test_lifecycle_exceptions_have_stable_contracts(
    error: BillingError,
    expected_code: str,
    expected_message: str,
) -> None:
    assert error.code == expected_code
    assert error.public_message == expected_message
