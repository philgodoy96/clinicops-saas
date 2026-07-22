from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.billing.catalog import (
    PriceDefinition,
    get_price_definition,
)
from clinicops.billing.enums import (
    BillingProvider,
    BillingReconciliationOutcome,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingReconciliationConflictError,
    BillingReconciliationInvalidSnapshotError,
    BillingReconciliationProviderIdentityMismatchError,
    BillingReconciliationProviderStateNotFoundError,
    BillingReconciliationSubscriptionNotFoundError,
    BillingReconciliationUnsupportedPriceError,
    UnsupportedPriceCodeError,
)
from clinicops.billing.models import Subscription
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
)

if TYPE_CHECKING:
    from clinicops.billing.providers.base import PaymentProvider


@dataclass(frozen=True, slots=True)
class ReconcileBillingSubscriptionCommand:
    """Identify one local subscription to reconcile."""

    subscription_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.subscription_id, UUID):
            raise TypeError("The billing subscription ID must be a UUID.")


@dataclass(frozen=True, slots=True)
class BillingProviderSubscriptionSnapshot:
    """Canonical provider-owned subscription state."""

    provider: BillingProvider
    provider_subscription_id: str
    provider_state_version: int
    price_code: str
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    canceled_at: datetime | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.provider, BillingProvider):
            raise TypeError("The billing provider must be a BillingProvider.")

        if not isinstance(self.status, SubscriptionStatus):
            raise TypeError("The provider subscription status must be a SubscriptionStatus.")

        normalized_provider_subscription_id = self.provider_subscription_id.strip()
        normalized_price_code = self.price_code.strip()

        if not normalized_provider_subscription_id:
            raise ValueError("The provider subscription ID must not be empty.")

        if not normalized_price_code:
            raise ValueError("The provider price code must not be empty.")

        object.__setattr__(
            self,
            "provider_subscription_id",
            normalized_provider_subscription_id,
        )
        object.__setattr__(
            self,
            "price_code",
            normalized_price_code,
        )

        if self.provider_state_version < 1:
            raise ValueError("The provider state version must be positive.")

        _require_timezone_aware(
            self.current_period_start,
            field_name="current_period_start",
        )
        _require_timezone_aware(
            self.current_period_end,
            field_name="current_period_end",
        )
        _require_timezone_aware(
            self.observed_at,
            field_name="observed_at",
        )

        if self.current_period_end <= self.current_period_start:
            raise ValueError("The provider billing period end must be after its start.")

        supported_statuses = {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.CANCELED,
        }

        if self.status not in supported_statuses:
            raise ValueError(
                "The provider subscription snapshot status is not supported for reconciliation."
            )

        if self.status is SubscriptionStatus.ACTIVE:
            if self.canceled_at is not None:
                raise ValueError(
                    "An active provider subscription snapshot must not include canceled_at."
                )
            return

        if self.cancel_at_period_end:
            raise ValueError(
                "A canceled provider subscription snapshot "
                "must not remain scheduled for cancellation."
            )

        if self.canceled_at is None:
            raise ValueError("A canceled provider subscription snapshot must include canceled_at.")

        _require_timezone_aware(
            self.canceled_at,
            field_name="canceled_at",
        )

        if not (self.current_period_start <= self.canceled_at <= self.current_period_end):
            raise ValueError(
                "The provider cancellation timestamp must fall inside the billing period."
            )


@dataclass(frozen=True, slots=True)
class ReconciledBillingSubscription:
    """Completed reconciliation comparison and repair result."""

    subscription_id: UUID
    provider: BillingProvider
    provider_subscription_id: str
    outcome: BillingReconciliationOutcome
    drift_fields: tuple[str, ...]
    previous_provider_state_version: int
    provider_state_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.subscription_id, UUID):
            raise TypeError("The reconciled subscription ID must be a UUID.")

        if not isinstance(self.provider, BillingProvider):
            raise TypeError("The reconciled provider must be a BillingProvider.")

        normalized_provider_subscription_id = self.provider_subscription_id.strip()

        if not normalized_provider_subscription_id:
            raise ValueError("The provider subscription ID must not be empty.")

        object.__setattr__(
            self,
            "provider_subscription_id",
            normalized_provider_subscription_id,
        )

        if self.previous_provider_state_version < 1:
            raise ValueError("The previous provider state version must be positive.")

        if self.provider_state_version < 1:
            raise ValueError("The provider state version must be positive.")

        normalized_drift_fields = tuple(field_name.strip() for field_name in self.drift_fields)

        if any(not field_name for field_name in normalized_drift_fields):
            raise ValueError("Reconciliation drift fields must not be empty.")

        if len(set(normalized_drift_fields)) != len(normalized_drift_fields):
            raise ValueError("Reconciliation drift fields must be unique.")

        object.__setattr__(
            self,
            "drift_fields",
            tuple(sorted(normalized_drift_fields)),
        )

        if self.outcome is BillingReconciliationOutcome.IN_SYNC:
            if self.drift_fields:
                raise ValueError("An in-sync reconciliation result must not contain drift fields.")

            if self.provider_state_version != self.previous_provider_state_version:
                raise ValueError(
                    "An in-sync reconciliation result must preserve the provider state version."
                )
            return

        if self.outcome is BillingReconciliationOutcome.REPAIRED:
            if not self.drift_fields:
                raise ValueError(
                    "A repaired reconciliation result must contain at least one drift field."
                )

            if self.provider_state_version < self.previous_provider_state_version:
                raise ValueError(
                    "A repaired reconciliation result cannot regress the provider state version."
                )
            return

        if (
            self.outcome is BillingReconciliationOutcome.IGNORED
            and self.provider_state_version >= self.previous_provider_state_version
        ):
            raise ValueError(
                "An ignored reconciliation result must reference an older provider snapshot."
            )


def _require_timezone_aware(
    value: datetime,
    *,
    field_name: str,
) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class _LocalProviderIdentity:
    subscription_id: UUID
    provider: BillingProvider
    provider_subscription_id: str


class ReconcileBillingSubscriptionService:
    """Compare and safely repair one local subscription."""

    def __init__(
        self,
        payment_provider: PaymentProvider,
        *,
        subscription_repository: (SubscriptionRepository | None) = None,
    ) -> None:
        self._payment_provider = payment_provider
        self._subscription_repository = (
            subscription_repository
            if subscription_repository is not None
            else SubscriptionRepository()
        )

    def execute(
        self,
        session: Session,
        command: ReconcileBillingSubscriptionCommand,
    ) -> ReconciledBillingSubscription:
        """Fetch provider state outside the local lock transaction."""

        try:
            identity = self._load_local_identity(
                session=session,
                subscription_id=command.subscription_id,
            )
            session.commit()
        except Exception:
            _rollback_if_active(session)
            raise

        if self._payment_provider.provider is not identity.provider:
            raise (BillingReconciliationProviderIdentityMismatchError())

        snapshot = self._payment_provider.get_subscription_snapshot(
            identity.provider_subscription_id
        )

        if snapshot is None:
            raise BillingReconciliationProviderStateNotFoundError(
                provider_subscription_id=(identity.provider_subscription_id)
            )

        if not isinstance(
            snapshot,
            BillingProviderSubscriptionSnapshot,
        ):
            raise BillingReconciliationInvalidSnapshotError(
                internal_message=(
                    "The payment provider returned an unsupported subscription snapshot object."
                )
            )

        _validate_snapshot_identity(
            snapshot=snapshot,
            identity=identity,
        )

        try:
            subscription = self._subscription_repository.get_by_id_for_update(
                session,
                subscription_id=(identity.subscription_id),
            )

            if subscription is None:
                raise (BillingReconciliationSubscriptionNotFoundError())

            _validate_locked_subscription_identity(
                subscription=subscription,
                identity=identity,
            )

            result = _reconcile_subscription(
                subscription=subscription,
                snapshot=snapshot,
            )

            if result.outcome is BillingReconciliationOutcome.REPAIRED:
                self._subscription_repository.flush(session)

            session.commit()
            return result
        except Exception:
            _rollback_if_active(session)
            raise

    def _load_local_identity(
        self,
        *,
        session: Session,
        subscription_id: UUID,
    ) -> _LocalProviderIdentity:
        subscription = self._subscription_repository.get_by_id(
            session,
            subscription_id=subscription_id,
        )

        if subscription is None:
            raise BillingReconciliationSubscriptionNotFoundError()

        provider_subscription_id = subscription.provider_subscription_id

        if provider_subscription_id is None:
            raise BillingReconciliationConflictError(
                conflict_code=("provider_subscription_identity_missing")
            )

        normalized_provider_subscription_id = provider_subscription_id.strip()

        if not normalized_provider_subscription_id:
            raise BillingReconciliationConflictError(
                conflict_code=("provider_subscription_identity_missing")
            )

        if subscription.provider_state_version < 1:
            raise BillingReconciliationConflictError(
                conflict_code=("invalid_local_provider_state_version")
            )

        return _LocalProviderIdentity(
            subscription_id=subscription.id,
            provider=subscription.provider,
            provider_subscription_id=(normalized_provider_subscription_id),
        )


def _reconcile_subscription(
    *,
    subscription: Subscription,
    snapshot: BillingProviderSubscriptionSnapshot,
) -> ReconciledBillingSubscription:
    previous_provider_state_version = subscription.provider_state_version

    if snapshot.provider_state_version < previous_provider_state_version:
        return ReconciledBillingSubscription(
            subscription_id=subscription.id,
            provider=subscription.provider,
            provider_subscription_id=(snapshot.provider_subscription_id),
            outcome=BillingReconciliationOutcome.IGNORED,
            drift_fields=("provider_state_version",),
            previous_provider_state_version=(previous_provider_state_version),
            provider_state_version=(snapshot.provider_state_version),
        )

    if snapshot.status is SubscriptionStatus.ACTIVE:
        return _reconcile_active_snapshot(
            subscription=subscription,
            snapshot=snapshot,
            previous_provider_state_version=(previous_provider_state_version),
        )

    if snapshot.status is SubscriptionStatus.CANCELED:
        return _reconcile_canceled_snapshot(
            subscription=subscription,
            snapshot=snapshot,
            previous_provider_state_version=(previous_provider_state_version),
        )

    raise BillingReconciliationInvalidSnapshotError(
        internal_message=(
            "The provider snapshot status is not supported by billing reconciliation."
        )
    )


def _reconcile_active_snapshot(
    *,
    subscription: Subscription,
    snapshot: BillingProviderSubscriptionSnapshot,
    previous_provider_state_version: int,
) -> ReconciledBillingSubscription:
    if subscription.status is SubscriptionStatus.CANCELED or subscription.canceled_at is not None:
        raise BillingReconciliationConflictError(conflict_code="reactivation_not_supported")

    if subscription.status not in {
        SubscriptionStatus.PENDING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
    }:
        raise BillingReconciliationConflictError(
            conflict_code="unsupported_local_subscription_status"
        )

    _validate_nonregressing_period(
        subscription=subscription,
        snapshot=snapshot,
    )

    expected_pending_price_code = _resolve_expected_pending_price_code(
        subscription=subscription,
        snapshot=snapshot,
        clear_pending=(snapshot.cancel_at_period_end),
    )
    price = _resolve_snapshot_price(snapshot)

    expected_values: dict[str, object] = {
        "price_code": price.price_code,
        "plan": price.plan,
        "billing_interval": price.billing_interval,
        "currency": price.currency,
        "unit_amount": price.unit_amount,
        "pending_price_code": (expected_pending_price_code),
        "status": SubscriptionStatus.ACTIVE,
        "current_period_start": (snapshot.current_period_start),
        "current_period_end": snapshot.current_period_end,
        "cancel_at_period_end": (snapshot.cancel_at_period_end),
        "canceled_at": None,
        "provider_state_version": (snapshot.provider_state_version),
    }

    return _compare_and_repair(
        subscription=subscription,
        snapshot=snapshot,
        expected_values=expected_values,
        previous_provider_state_version=(previous_provider_state_version),
    )


def _reconcile_canceled_snapshot(
    *,
    subscription: Subscription,
    snapshot: BillingProviderSubscriptionSnapshot,
    previous_provider_state_version: int,
) -> ReconciledBillingSubscription:
    if subscription.status not in {
        SubscriptionStatus.PENDING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
        SubscriptionStatus.CANCELED,
    }:
        raise BillingReconciliationConflictError(
            conflict_code="unsupported_local_subscription_status"
        )

    _validate_nonregressing_period(
        subscription=subscription,
        snapshot=snapshot,
    )
    _validate_scheduled_cancellation_boundary(
        subscription=subscription,
        snapshot=snapshot,
    )
    _resolve_expected_pending_price_code(
        subscription=subscription,
        snapshot=snapshot,
        clear_pending=True,
    )
    price = _resolve_snapshot_price(snapshot)

    expected_values: dict[str, object] = {
        "price_code": price.price_code,
        "plan": price.plan,
        "billing_interval": price.billing_interval,
        "currency": price.currency,
        "unit_amount": price.unit_amount,
        "pending_price_code": None,
        "status": SubscriptionStatus.CANCELED,
        "current_period_start": (snapshot.current_period_start),
        "current_period_end": snapshot.current_period_end,
        "cancel_at_period_end": False,
        "canceled_at": snapshot.canceled_at,
        "provider_state_version": (snapshot.provider_state_version),
    }

    return _compare_and_repair(
        subscription=subscription,
        snapshot=snapshot,
        expected_values=expected_values,
        previous_provider_state_version=(previous_provider_state_version),
    )


def _resolve_expected_pending_price_code(
    *,
    subscription: Subscription,
    snapshot: BillingProviderSubscriptionSnapshot,
    clear_pending: bool,
) -> str | None:
    if snapshot.price_code == subscription.price_code:
        return None if clear_pending else subscription.pending_price_code

    if (
        subscription.pending_price_code is not None
        and snapshot.price_code == subscription.pending_price_code
    ):
        return None

    raise BillingReconciliationConflictError(conflict_code="unexpected_provider_price")


def _resolve_snapshot_price(
    snapshot: BillingProviderSubscriptionSnapshot,
) -> PriceDefinition:
    try:
        return get_price_definition(snapshot.price_code)
    except UnsupportedPriceCodeError as error:
        raise BillingReconciliationUnsupportedPriceError(price_code=snapshot.price_code) from error


def _compare_and_repair(
    *,
    subscription: Subscription,
    snapshot: BillingProviderSubscriptionSnapshot,
    expected_values: dict[str, object],
    previous_provider_state_version: int,
) -> ReconciledBillingSubscription:
    drift_fields = [
        field_name
        for field_name, expected_value in expected_values.items()
        if getattr(subscription, field_name) != expected_value
    ]

    if not drift_fields:
        return ReconciledBillingSubscription(
            subscription_id=subscription.id,
            provider=subscription.provider,
            provider_subscription_id=(snapshot.provider_subscription_id),
            outcome=BillingReconciliationOutcome.IN_SYNC,
            drift_fields=(),
            previous_provider_state_version=(previous_provider_state_version),
            provider_state_version=(snapshot.provider_state_version),
        )

    if subscription.last_provider_event_at != snapshot.observed_at:
        drift_fields.append("last_provider_event_at")

    for field_name, expected_value in expected_values.items():
        setattr(subscription, field_name, expected_value)

    subscription.last_provider_event_at = snapshot.observed_at

    return ReconciledBillingSubscription(
        subscription_id=subscription.id,
        provider=subscription.provider,
        provider_subscription_id=(snapshot.provider_subscription_id),
        outcome=BillingReconciliationOutcome.REPAIRED,
        drift_fields=tuple(drift_fields),
        previous_provider_state_version=(previous_provider_state_version),
        provider_state_version=(snapshot.provider_state_version),
    )


def _validate_scheduled_cancellation_boundary(
    *,
    subscription: Subscription,
    snapshot: BillingProviderSubscriptionSnapshot,
) -> None:
    if not subscription.cancel_at_period_end:
        return

    local_period_end = subscription.current_period_end

    if (
        local_period_end is None
        or snapshot.current_period_end != local_period_end
        or snapshot.canceled_at != local_period_end
    ):
        raise BillingReconciliationConflictError(
            conflict_code=("scheduled_cancellation_boundary_mismatch")
        )


def _validate_snapshot_identity(
    *,
    snapshot: BillingProviderSubscriptionSnapshot,
    identity: _LocalProviderIdentity,
) -> None:
    if (
        snapshot.provider is not identity.provider
        or snapshot.provider_subscription_id != identity.provider_subscription_id
    ):
        raise (BillingReconciliationProviderIdentityMismatchError())


def _validate_locked_subscription_identity(
    *,
    subscription: Subscription,
    identity: _LocalProviderIdentity,
) -> None:
    if (
        subscription.id != identity.subscription_id
        or subscription.provider is not identity.provider
        or subscription.provider_subscription_id != identity.provider_subscription_id
    ):
        raise (BillingReconciliationProviderIdentityMismatchError())


def _validate_nonregressing_period(
    *,
    subscription: Subscription,
    snapshot: BillingProviderSubscriptionSnapshot,
) -> None:
    local_period_start = subscription.current_period_start
    local_period_end = subscription.current_period_end

    if local_period_start is None and local_period_end is None:
        return

    if local_period_start is None or local_period_end is None:
        raise BillingReconciliationConflictError(conflict_code="incomplete_local_billing_period")

    if (
        snapshot.current_period_start < local_period_start
        or snapshot.current_period_end < local_period_end
    ):
        raise BillingReconciliationConflictError(conflict_code="provider_period_regression")


def _rollback_if_active(session: Session) -> None:
    if session.in_transaction():
        session.rollback()
