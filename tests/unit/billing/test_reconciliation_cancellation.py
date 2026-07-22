from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    BillingReconciliationOutcome,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingReconciliationConflictError,
)
from clinicops.billing.models import Subscription
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.reconciliation import (
    BillingProviderSubscriptionSnapshot,
    ReconcileBillingSubscriptionCommand,
    ReconcileBillingSubscriptionService,
    ReconciledBillingSubscription,
)
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
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
NEXT_PERIOD_END = datetime(
    2026,
    9,
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
OBSERVED_AT = datetime(
    2026,
    8,
    22,
    12,
    5,
    tzinfo=UTC,
)


class RecordingSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self._transaction_open = False

    def mark_transaction_open(self) -> None:
        self._transaction_open = True

    def commit(self) -> None:
        self.commit_count += 1
        self._transaction_open = False

    def rollback(self) -> None:
        self.rollback_count += 1
        self._transaction_open = False

    def in_transaction(self) -> bool:
        return self._transaction_open


class RecordingSubscriptionRepository:
    def __init__(
        self,
        subscription: Subscription,
    ) -> None:
        self.subscription = subscription
        self.flush_count = 0

    def get_by_id(
        self,
        session: Session,
        *,
        subscription_id: UUID,
    ) -> Subscription | None:
        cast(
            RecordingSession,
            session,
        ).mark_transaction_open()

        if self.subscription.id == subscription_id:
            return self.subscription

        return None

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        subscription_id: UUID,
    ) -> Subscription | None:
        cast(
            RecordingSession,
            session,
        ).mark_transaction_open()

        if self.subscription.id == subscription_id:
            return self.subscription

        return None

    def flush(self, session: Session) -> None:
        self.flush_count += 1


class RecordingPaymentProvider:
    provider = BillingProvider.FAKE

    def __init__(
        self,
        *,
        session: RecordingSession,
        snapshot: BillingProviderSubscriptionSnapshot,
    ) -> None:
        self._session = session
        self._snapshot = snapshot

    def get_subscription_snapshot(
        self,
        provider_subscription_id: str,
    ) -> BillingProviderSubscriptionSnapshot | None:
        assert not self._session.in_transaction()
        assert provider_subscription_id == "fake_sub_01"
        return self._snapshot


def _subscription(
    *,
    provider_state_version: int = 4,
    price_code: str = "starter_monthly",
    plan: BillingPlan = BillingPlan.STARTER,
    unit_amount: int = 4900,
    pending_price_code: str | None = None,
    status: SubscriptionStatus = (SubscriptionStatus.ACTIVE),
    cancel_at_period_end: bool = False,
    cancellation_requested_at: datetime | None = None,
    canceled_at: datetime | None = None,
    current_period_start: datetime = PERIOD_START,
    current_period_end: datetime = PERIOD_END,
) -> Subscription:
    return Subscription(
        id=uuid4(),
        tenant_id=uuid4(),
        billing_customer_id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_subscription_id="fake_sub_01",
        price_code=price_code,
        plan=plan,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=unit_amount,
        pending_price_code=pending_price_code,
        status=status,
        cancel_at_period_end=cancel_at_period_end,
        cancellation_requested_at=(cancellation_requested_at),
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        provider_state_version=provider_state_version,
        last_provider_event_at=None,
        canceled_at=canceled_at,
    )


def _snapshot(
    *,
    provider_state_version: int = 5,
    price_code: str = "starter_monthly",
    status: SubscriptionStatus = (SubscriptionStatus.ACTIVE),
    current_period_start: datetime = PERIOD_START,
    current_period_end: datetime = PERIOD_END,
    cancel_at_period_end: bool = False,
    canceled_at: datetime | None = None,
) -> BillingProviderSubscriptionSnapshot:
    return BillingProviderSubscriptionSnapshot(
        provider=BillingProvider.FAKE,
        provider_subscription_id="fake_sub_01",
        provider_state_version=(provider_state_version),
        price_code=price_code,
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
        canceled_at=canceled_at,
        observed_at=OBSERVED_AT,
    )


def _execute(
    *,
    subscription: Subscription,
    snapshot: BillingProviderSubscriptionSnapshot,
) -> tuple[
    ReconciledBillingSubscription,
    RecordingSession,
    RecordingSubscriptionRepository,
]:
    session = RecordingSession()
    repository = RecordingSubscriptionRepository(subscription)
    provider = RecordingPaymentProvider(
        session=session,
        snapshot=snapshot,
    )
    service = ReconcileBillingSubscriptionService(
        payment_provider=cast(
            PaymentProvider,
            provider,
        ),
        subscription_repository=cast(
            SubscriptionRepository,
            repository,
        ),
    )

    result = service.execute(
        cast(Session, session),
        ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
    )

    return result, session, repository


def test_scheduled_cancellation_repairs_missing_local_flag() -> None:
    subscription = _subscription(
        cancellation_requested_at=REQUESTED_AT,
        pending_price_code="professional_monthly",
    )

    result, session, repository = _execute(
        subscription=subscription,
        snapshot=_snapshot(cancel_at_period_end=True),
    )

    assert result.outcome is BillingReconciliationOutcome.REPAIRED
    assert result.drift_fields == (
        "cancel_at_period_end",
        "last_provider_event_at",
        "pending_price_code",
        "provider_state_version",
    )
    assert subscription.cancel_at_period_end is True
    assert subscription.cancellation_requested_at == (REQUESTED_AT)
    assert subscription.pending_price_code is None
    assert subscription.canceled_at is None
    assert subscription.provider_state_version == 5
    assert subscription.last_provider_event_at == (OBSERVED_AT)
    assert session.commit_count == 2
    assert repository.flush_count == 1


def test_scheduled_cancellation_does_not_invent_request_timestamp() -> None:
    subscription = _subscription(cancellation_requested_at=None)

    result, _, _ = _execute(
        subscription=subscription,
        snapshot=_snapshot(cancel_at_period_end=True),
    )

    assert result.outcome is BillingReconciliationOutcome.REPAIRED
    assert subscription.cancel_at_period_end is True
    assert subscription.cancellation_requested_at is None


def test_active_snapshot_clears_stale_local_schedule_but_preserves_audit() -> None:
    subscription = _subscription(
        cancel_at_period_end=True,
        cancellation_requested_at=REQUESTED_AT,
    )

    result, _, _ = _execute(
        subscription=subscription,
        snapshot=_snapshot(cancel_at_period_end=False),
    )

    assert result.outcome is BillingReconciliationOutcome.REPAIRED
    assert subscription.cancel_at_period_end is False
    assert subscription.cancellation_requested_at == (REQUESTED_AT)
    assert subscription.canceled_at is None


def test_final_cancellation_repairs_local_state() -> None:
    subscription = _subscription(
        cancel_at_period_end=True,
        cancellation_requested_at=REQUESTED_AT,
        pending_price_code="professional_monthly",
    )

    result, _, repository = _execute(
        subscription=subscription,
        snapshot=_snapshot(
            status=SubscriptionStatus.CANCELED,
            cancel_at_period_end=False,
            canceled_at=PERIOD_END,
        ),
    )

    assert result.outcome is BillingReconciliationOutcome.REPAIRED
    assert subscription.status is SubscriptionStatus.CANCELED
    assert subscription.canceled_at == PERIOD_END
    assert subscription.cancel_at_period_end is False
    assert subscription.cancellation_requested_at == (REQUESTED_AT)
    assert subscription.pending_price_code is None
    assert subscription.provider_state_version == 5
    assert subscription.last_provider_event_at == (OBSERVED_AT)
    assert repository.flush_count == 1


def test_provider_authoritative_mid_period_cancellation_is_repaired() -> None:
    subscription = _subscription(cancellation_requested_at=None)

    result, _, _ = _execute(
        subscription=subscription,
        snapshot=_snapshot(
            status=SubscriptionStatus.CANCELED,
            cancel_at_period_end=False,
            canceled_at=MID_PERIOD,
        ),
    )

    assert result.outcome is BillingReconciliationOutcome.REPAIRED
    assert subscription.status is SubscriptionStatus.CANCELED
    assert subscription.canceled_at == MID_PERIOD
    assert subscription.cancellation_requested_at is None


def test_final_cancellation_can_activate_pending_provider_price() -> None:
    subscription = _subscription(pending_price_code="professional_monthly")

    result, _, _ = _execute(
        subscription=subscription,
        snapshot=_snapshot(
            price_code="professional_monthly",
            status=SubscriptionStatus.CANCELED,
            cancel_at_period_end=False,
            canceled_at=PERIOD_END,
        ),
    )

    assert result.outcome is BillingReconciliationOutcome.REPAIRED
    assert subscription.price_code == ("professional_monthly")
    assert subscription.plan is BillingPlan.PROFESSIONAL
    assert subscription.unit_amount == 9900
    assert subscription.pending_price_code is None


def test_already_canceled_subscription_can_be_in_sync() -> None:
    subscription = _subscription(
        provider_state_version=5,
        status=SubscriptionStatus.CANCELED,
        cancel_at_period_end=False,
        canceled_at=PERIOD_END,
    )

    result, _, repository = _execute(
        subscription=subscription,
        snapshot=_snapshot(
            provider_state_version=5,
            status=SubscriptionStatus.CANCELED,
            cancel_at_period_end=False,
            canceled_at=PERIOD_END,
        ),
    )

    assert result.outcome is BillingReconciliationOutcome.IN_SYNC
    assert result.drift_fields == ()
    assert repository.flush_count == 0
    assert subscription.last_provider_event_at is None


def test_older_cancellation_snapshot_is_ignored() -> None:
    subscription = _subscription(provider_state_version=6)

    result, _, repository = _execute(
        subscription=subscription,
        snapshot=_snapshot(
            provider_state_version=5,
            status=SubscriptionStatus.CANCELED,
            cancel_at_period_end=False,
            canceled_at=PERIOD_END,
        ),
    )

    assert result.outcome is BillingReconciliationOutcome.IGNORED
    assert repository.flush_count == 0
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.canceled_at is None
    assert subscription.provider_state_version == 6


def test_provider_active_state_cannot_reactivate_local_cancellation() -> None:
    subscription = _subscription(
        status=SubscriptionStatus.CANCELED,
        canceled_at=PERIOD_END,
    )
    session = RecordingSession()
    repository = RecordingSubscriptionRepository(subscription)
    provider = RecordingPaymentProvider(
        session=session,
        snapshot=_snapshot(),
    )
    service = ReconcileBillingSubscriptionService(
        payment_provider=cast(
            PaymentProvider,
            provider,
        ),
        subscription_repository=cast(
            SubscriptionRepository,
            repository,
        ),
    )

    with pytest.raises(BillingReconciliationConflictError) as exception_info:
        service.execute(
            cast(Session, session),
            ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
        )

    assert exception_info.value.conflict_code == "reactivation_not_supported"
    assert session.rollback_count == 1
    assert repository.flush_count == 0


def test_scheduled_local_cancellation_requires_matching_final_boundary() -> None:
    subscription = _subscription(
        cancel_at_period_end=True,
        cancellation_requested_at=REQUESTED_AT,
    )
    session = RecordingSession()
    repository = RecordingSubscriptionRepository(subscription)
    provider = RecordingPaymentProvider(
        session=session,
        snapshot=_snapshot(
            status=SubscriptionStatus.CANCELED,
            current_period_start=PERIOD_END,
            current_period_end=NEXT_PERIOD_END,
            cancel_at_period_end=False,
            canceled_at=NEXT_PERIOD_END,
        ),
    )
    service = ReconcileBillingSubscriptionService(
        payment_provider=cast(
            PaymentProvider,
            provider,
        ),
        subscription_repository=cast(
            SubscriptionRepository,
            repository,
        ),
    )

    with pytest.raises(BillingReconciliationConflictError) as exception_info:
        service.execute(
            cast(Session, session),
            ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
        )

    assert exception_info.value.conflict_code == "scheduled_cancellation_boundary_mismatch"
    assert repository.flush_count == 0


def test_canceled_snapshot_rejects_unrelated_provider_price() -> None:
    subscription = _subscription()
    session = RecordingSession()
    repository = RecordingSubscriptionRepository(subscription)
    provider = RecordingPaymentProvider(
        session=session,
        snapshot=_snapshot(
            price_code="professional_monthly",
            status=SubscriptionStatus.CANCELED,
            cancel_at_period_end=False,
            canceled_at=PERIOD_END,
        ),
    )
    service = ReconcileBillingSubscriptionService(
        payment_provider=cast(
            PaymentProvider,
            provider,
        ),
        subscription_repository=cast(
            SubscriptionRepository,
            repository,
        ),
    )

    with pytest.raises(BillingReconciliationConflictError) as exception_info:
        service.execute(
            cast(Session, session),
            ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
        )

    assert exception_info.value.conflict_code == "unexpected_provider_price"
    assert repository.flush_count == 0
