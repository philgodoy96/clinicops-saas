from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from clinicops.billing.catalog import get_price_definition
from clinicops.billing.enums import SubscriptionStatus
from clinicops.billing.exceptions import (
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

_ALLOWED_SUBSCRIPTION_TRANSITIONS: Final[
    Mapping[
        SubscriptionStatus,
        frozenset[SubscriptionStatus],
    ]
] = {
    SubscriptionStatus.PENDING: frozenset(
        {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.CANCELED,
        }
    ),
    SubscriptionStatus.ACTIVE: frozenset(
        {
            SubscriptionStatus.PAST_DUE,
            SubscriptionStatus.CANCELED,
        }
    ),
    SubscriptionStatus.PAST_DUE: frozenset(
        {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.CANCELED,
        }
    ),
    SubscriptionStatus.CANCELED: frozenset(),
}

_MUTABLE_SUBSCRIPTION_STATUSES: Final = frozenset(
    {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
    }
)


@dataclass(frozen=True, slots=True)
class SubscriptionLifecycleState:
    """Immutable snapshot used by pure subscription lifecycle rules."""

    status: SubscriptionStatus
    price_code: str
    current_period_end: datetime
    pending_price_code: str | None = None
    cancel_at_period_end: bool = False
    cancellation_requested_at: datetime | None = None
    canceled_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_timezone_aware(
            self.current_period_end,
            field_name="current_period_end",
        )

        if self.cancellation_requested_at is not None:
            _require_timezone_aware(
                self.cancellation_requested_at,
                field_name="cancellation_requested_at",
            )

        if self.canceled_at is not None:
            _require_timezone_aware(
                self.canceled_at,
                field_name="canceled_at",
            )


def validate_subscription_transition(
    current_status: SubscriptionStatus,
    target_status: SubscriptionStatus,
) -> None:
    """Validate a confirmed subscription status transition."""

    if current_status is target_status:
        return

    allowed_targets = _ALLOWED_SUBSCRIPTION_TRANSITIONS[current_status]

    if target_status not in allowed_targets:
        raise InvalidSubscriptionTransitionError(
            current_status=current_status,
            target_status=target_status,
        )


def is_stale_provider_state_version(
    *,
    current_version: int,
    incoming_version: int,
) -> bool:
    """Return whether an incoming provider state predates local state."""

    if current_version < 0:
        raise ValueError("current_version must be greater than or equal to zero.")

    if incoming_version < 0:
        raise ValueError("incoming_version must be greater than or equal to zero.")

    return incoming_version < current_version


def schedule_price_change(
    state: SubscriptionLifecycleState,
    *,
    target_price_code: str,
) -> SubscriptionLifecycleState:
    """Schedule a supported price change for the next period."""

    get_price_definition(target_price_code)

    if state.status is SubscriptionStatus.CANCELED:
        raise CanceledSubscriptionMutationError()

    if state.status not in _MUTABLE_SUBSCRIPTION_STATUSES:
        raise SubscriptionMutationNotAllowedError(
            status=state.status,
            operation="schedule_price_change",
        )

    if state.cancel_at_period_end:
        raise CancellationAlreadyRequestedError()

    if state.pending_price_code is not None:
        raise PendingPlanChangeError(state.pending_price_code)

    if target_price_code == state.price_code:
        raise PlanAlreadySelectedError(target_price_code)

    return replace(
        state,
        pending_price_code=target_price_code,
    )


def request_period_end_cancellation(
    state: SubscriptionLifecycleState,
    *,
    requested_at: datetime,
) -> SubscriptionLifecycleState:
    """Schedule cancellation and preserve access until period end."""

    _require_timezone_aware(
        requested_at,
        field_name="requested_at",
    )

    if state.status is SubscriptionStatus.CANCELED:
        raise CanceledSubscriptionMutationError()

    if state.status not in _MUTABLE_SUBSCRIPTION_STATUSES:
        raise SubscriptionMutationNotAllowedError(
            status=state.status,
            operation="request_period_end_cancellation",
        )

    if state.cancel_at_period_end:
        raise CancellationAlreadyRequestedError()

    return replace(
        state,
        pending_price_code=None,
        cancel_at_period_end=True,
        cancellation_requested_at=requested_at,
    )


def confirm_scheduled_price_change(
    state: SubscriptionLifecycleState,
    *,
    confirmed_at: datetime,
    next_period_end: datetime,
) -> SubscriptionLifecycleState:
    """Apply a scheduled price after the current period ends."""

    _require_timezone_aware(
        confirmed_at,
        field_name="confirmed_at",
    )
    _require_timezone_aware(
        next_period_end,
        field_name="next_period_end",
    )

    if state.status is SubscriptionStatus.CANCELED:
        raise CanceledSubscriptionMutationError()

    if state.cancel_at_period_end:
        raise CancellationAlreadyRequestedError()

    pending_price_code = state.pending_price_code

    if pending_price_code is None:
        raise PendingPlanChangeNotFoundError()

    if confirmed_at < state.current_period_end:
        raise BillingPeriodBoundaryNotReachedError()

    if next_period_end <= confirmed_at:
        raise ValueError("next_period_end must be later than confirmed_at.")

    get_price_definition(pending_price_code)

    return replace(
        state,
        price_code=pending_price_code,
        pending_price_code=None,
        current_period_end=next_period_end,
    )


def finalize_period_end_cancellation(
    state: SubscriptionLifecycleState,
    *,
    canceled_at: datetime,
) -> SubscriptionLifecycleState:
    """Finalize a previously scheduled period-end cancellation."""

    _require_timezone_aware(
        canceled_at,
        field_name="canceled_at",
    )

    if state.status is SubscriptionStatus.CANCELED:
        return state

    if not state.cancel_at_period_end:
        raise CancellationNotRequestedError()

    if canceled_at < state.current_period_end:
        raise BillingPeriodBoundaryNotReachedError()

    validate_subscription_transition(
        state.status,
        SubscriptionStatus.CANCELED,
    )

    return replace(
        state,
        status=SubscriptionStatus.CANCELED,
        pending_price_code=None,
        cancel_at_period_end=False,
        canceled_at=canceled_at,
    )


def _require_timezone_aware(
    value: datetime,
    *,
    field_name: str,
) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
