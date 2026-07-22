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
    BillingReconciliationProviderIdentityMismatchError,
    BillingReconciliationProviderStateNotFoundError,
    BillingReconciliationSubscriptionNotFoundError,
)
from clinicops.billing.models import Subscription
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.reconciliation import (
    BillingProviderSubscriptionSnapshot,
    ReconcileBillingSubscriptionCommand,
    ReconcileBillingSubscriptionService,
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
        *,
        initial_subscription: Subscription | None,
        locked_subscription: Subscription | None = None,
    ) -> None:
        self.initial_subscription = initial_subscription
        self.locked_subscription = (
            locked_subscription if locked_subscription is not None else initial_subscription
        )
        self.read_count = 0
        self.lock_count = 0
        self.flush_count = 0

    def get_by_id(
        self,
        session: Session,
        *,
        subscription_id: UUID,
    ) -> Subscription | None:
        recording_session = cast(
            RecordingSession,
            session,
        )
        recording_session.mark_transaction_open()
        self.read_count += 1

        if (
            self.initial_subscription is not None
            and self.initial_subscription.id == subscription_id
        ):
            return self.initial_subscription

        return None

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        subscription_id: UUID,
    ) -> Subscription | None:
        recording_session = cast(
            RecordingSession,
            session,
        )
        recording_session.mark_transaction_open()
        self.lock_count += 1

        if self.locked_subscription is not None and self.locked_subscription.id == subscription_id:
            return self.locked_subscription

        return None

    def flush(self, session: Session) -> None:
        self.flush_count += 1


class RecordingPaymentProvider:
    def __init__(
        self,
        *,
        session: RecordingSession,
        snapshot: (BillingProviderSubscriptionSnapshot | None),
        provider: BillingProvider = BillingProvider.FAKE,
    ) -> None:
        self._session = session
        self._snapshot = snapshot
        self._provider = provider
        self.call_count = 0
        self.requested_provider_subscription_id: str | None = None

    @property
    def provider(self) -> BillingProvider:
        return self._provider

    def get_subscription_snapshot(
        self,
        provider_subscription_id: str,
    ) -> BillingProviderSubscriptionSnapshot | None:
        assert not self._session.in_transaction()
        self.call_count += 1
        self.requested_provider_subscription_id = provider_subscription_id
        return self._snapshot


def _subscription(
    *,
    provider_state_version: int = 4,
    price_code: str = "starter_monthly",
    plan: BillingPlan = BillingPlan.STARTER,
    unit_amount: int = 4900,
    pending_price_code: str | None = None,
    status: SubscriptionStatus = (SubscriptionStatus.ACTIVE),
    current_period_start: datetime | None = PERIOD_START,
    current_period_end: datetime | None = PERIOD_END,
    cancel_at_period_end: bool = False,
    cancellation_requested_at: datetime | None = None,
    canceled_at: datetime | None = None,
    provider: BillingProvider = BillingProvider.FAKE,
    provider_subscription_id: str = "fake_sub_01",
) -> Subscription:
    return Subscription(
        id=uuid4(),
        tenant_id=uuid4(),
        billing_customer_id=uuid4(),
        provider=provider,
        provider_subscription_id=(provider_subscription_id),
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
    provider_state_version: int = 4,
    price_code: str = "starter_monthly",
    status: SubscriptionStatus = (SubscriptionStatus.ACTIVE),
    current_period_start: datetime = PERIOD_START,
    current_period_end: datetime = PERIOD_END,
    cancel_at_period_end: bool = False,
    canceled_at: datetime | None = None,
    provider: BillingProvider = BillingProvider.FAKE,
    provider_subscription_id: str = "fake_sub_01",
) -> BillingProviderSubscriptionSnapshot:
    return BillingProviderSubscriptionSnapshot(
        provider=provider,
        provider_subscription_id=(provider_subscription_id),
        provider_state_version=(provider_state_version),
        price_code=price_code,
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
        canceled_at=canceled_at,
        observed_at=OBSERVED_AT,
    )


def _service(
    *,
    session: RecordingSession,
    repository: RecordingSubscriptionRepository,
    snapshot: (BillingProviderSubscriptionSnapshot | None),
    provider: BillingProvider = BillingProvider.FAKE,
) -> tuple[
    ReconcileBillingSubscriptionService,
    RecordingPaymentProvider,
]:
    payment_provider = RecordingPaymentProvider(
        session=session,
        snapshot=snapshot,
        provider=provider,
    )
    service = ReconcileBillingSubscriptionService(
        payment_provider=cast(
            PaymentProvider,
            payment_provider,
        ),
        subscription_repository=cast(
            SubscriptionRepository,
            repository,
        ),
    )

    return service, payment_provider


def test_in_sync_subscription_does_not_flush() -> None:
    session = RecordingSession()
    subscription = _subscription()
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, provider = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(),
    )

    result = service.execute(
        cast(Session, session),
        ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
    )

    assert session.commit_count == 2
    assert session.rollback_count == 0
    assert repository.read_count == 1
    assert repository.lock_count == 1
    assert repository.flush_count == 0
    assert provider.call_count == 1
    assert provider.requested_provider_subscription_id == "fake_sub_01"
    assert result.outcome is BillingReconciliationOutcome.IN_SYNC
    assert result.drift_fields == ()
    assert subscription.last_provider_event_at is None


def test_provider_call_occurs_after_read_transaction_commit() -> None:
    session = RecordingSession()
    subscription = _subscription()
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, provider = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(),
    )

    service.execute(
        cast(Session, session),
        ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
    )

    assert provider.call_count == 1
    assert session.commit_count == 2


def test_newer_snapshot_activates_pending_plan() -> None:
    session = RecordingSession()
    subscription = _subscription(pending_price_code="professional_monthly")
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, _ = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(
            provider_state_version=5,
            price_code="professional_monthly",
            current_period_start=PERIOD_END,
            current_period_end=NEXT_PERIOD_END,
        ),
    )

    result = service.execute(
        cast(Session, session),
        ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
    )

    assert result.outcome is BillingReconciliationOutcome.REPAIRED
    assert result.drift_fields == (
        "current_period_end",
        "current_period_start",
        "last_provider_event_at",
        "pending_price_code",
        "plan",
        "price_code",
        "provider_state_version",
        "unit_amount",
    )
    assert repository.flush_count == 1
    assert subscription.price_code == ("professional_monthly")
    assert subscription.plan is BillingPlan.PROFESSIONAL
    assert subscription.unit_amount == 9900
    assert subscription.pending_price_code is None
    assert subscription.current_period_start == PERIOD_END
    assert subscription.current_period_end == (NEXT_PERIOD_END)
    assert subscription.provider_state_version == 5
    assert subscription.last_provider_event_at == (OBSERVED_AT)


def test_current_provider_price_preserves_pending_plan() -> None:
    session = RecordingSession()
    subscription = _subscription(pending_price_code="professional_monthly")
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, _ = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(),
    )

    result = service.execute(
        cast(Session, session),
        ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
    )

    assert result.outcome is BillingReconciliationOutcome.IN_SYNC
    assert subscription.pending_price_code == ("professional_monthly")
    assert repository.flush_count == 0


def test_equal_version_repairs_local_catalog_drift() -> None:
    session = RecordingSession()
    subscription = _subscription(
        price_code="professional_monthly",
        plan=BillingPlan.STARTER,
        unit_amount=4900,
    )
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, _ = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(price_code="professional_monthly"),
    )

    result = service.execute(
        cast(Session, session),
        ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
    )

    assert result.outcome is BillingReconciliationOutcome.REPAIRED
    assert result.drift_fields == (
        "last_provider_event_at",
        "plan",
        "unit_amount",
    )
    assert subscription.plan is BillingPlan.PROFESSIONAL
    assert subscription.unit_amount == 9900
    assert subscription.provider_state_version == 4


def test_older_snapshot_is_ignored_without_mutation() -> None:
    session = RecordingSession()
    subscription = _subscription(provider_state_version=6)
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, _ = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(
            provider_state_version=5,
            current_period_start=PERIOD_END,
            current_period_end=NEXT_PERIOD_END,
        ),
    )

    result = service.execute(
        cast(Session, session),
        ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
    )

    assert result.outcome is BillingReconciliationOutcome.IGNORED
    assert result.drift_fields == ("provider_state_version",)
    assert repository.flush_count == 0
    assert subscription.provider_state_version == 6
    assert subscription.current_period_start == PERIOD_START
    assert subscription.current_period_end == PERIOD_END


def test_missing_local_subscription_stops_before_provider_call() -> None:
    session = RecordingSession()
    repository = RecordingSubscriptionRepository(initial_subscription=None)
    service, provider = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(),
    )

    with pytest.raises(BillingReconciliationSubscriptionNotFoundError):
        service.execute(
            cast(Session, session),
            ReconcileBillingSubscriptionCommand(subscription_id=uuid4()),
        )

    assert provider.call_count == 0
    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_missing_provider_snapshot_raises_stable_error() -> None:
    session = RecordingSession()
    subscription = _subscription()
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, _ = _service(
        session=session,
        repository=repository,
        snapshot=None,
    )

    with pytest.raises(BillingReconciliationProviderStateNotFoundError):
        service.execute(
            cast(Session, session),
            ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
        )

    assert session.commit_count == 1
    assert repository.lock_count == 0


def test_provider_identity_mismatch_is_rejected() -> None:
    session = RecordingSession()
    subscription = _subscription()
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, _ = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(provider_subscription_id="fake_sub_other"),
    )

    with pytest.raises(BillingReconciliationProviderIdentityMismatchError):
        service.execute(
            cast(Session, session),
            ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
        )

    assert repository.lock_count == 0


def test_identity_change_during_provider_fetch_rolls_back() -> None:
    session = RecordingSession()
    initial = _subscription()
    locked = _subscription(provider_subscription_id="fake_sub_other")
    locked.id = initial.id
    repository = RecordingSubscriptionRepository(
        initial_subscription=initial,
        locked_subscription=locked,
    )
    service, _ = _service(
        session=session,
        repository=repository,
        snapshot=_snapshot(),
    )

    with pytest.raises(BillingReconciliationProviderIdentityMismatchError):
        service.execute(
            cast(Session, session),
            ReconcileBillingSubscriptionCommand(subscription_id=initial.id),
        )

    assert session.commit_count == 1
    assert session.rollback_count == 1
    assert repository.flush_count == 0


@pytest.mark.parametrize(
    ("snapshot", "conflict_code"),
    [
        (
            _snapshot(price_code="professional_monthly"),
            "unexpected_provider_price",
        ),
        (
            _snapshot(
                provider_state_version=5,
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
            ),
            "provider_period_regression",
        ),
    ],
)
def test_unsafe_active_drift_is_rejected(
    snapshot: BillingProviderSubscriptionSnapshot,
    conflict_code: str,
) -> None:
    session = RecordingSession()
    subscription = _subscription()
    repository = RecordingSubscriptionRepository(initial_subscription=subscription)
    service, _ = _service(
        session=session,
        repository=repository,
        snapshot=snapshot,
    )

    with pytest.raises(BillingReconciliationConflictError) as exception_info:
        service.execute(
            cast(Session, session),
            ReconcileBillingSubscriptionCommand(subscription_id=subscription.id),
        )

    assert exception_info.value.conflict_code == conflict_code
    assert session.rollback_count == 1
    assert repository.flush_count == 0
