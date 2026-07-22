from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from threading import Barrier, Event
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    BillingReconciliationOutcome,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    SubscriptionStatus,
)
from clinicops.billing.models import (
    BillingCustomer,
    BillingWebhookEvent,
    Subscription,
)
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.providers.contracts import (
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
from clinicops.billing.reconciliation import (
    BillingProviderSubscriptionSnapshot,
    ReconcileBillingSubscriptionCommand,
    ReconcileBillingSubscriptionService,
    ReconciledBillingSubscription,
)
from clinicops.billing.webhooks.process import (
    ProcessBillingWebhookEventCommand,
    ProcessBillingWebhookEventService,
)
from clinicops.db.session import get_engine
from clinicops.tenancy.models import Tenant

PERIOD_START = datetime(
    2026,
    7,
    22,
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
WEBHOOK_PROCESSED_AT = datetime(
    2026,
    8,
    22,
    12,
    5,
    tzinfo=UTC,
)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


class FixedWebhookClock:
    def now(self) -> datetime:
        return WEBHOOK_PROCESSED_AT


class BlockingSnapshotProvider:
    provider = BillingProvider.FAKE

    def __init__(
        self,
        *,
        snapshot: BillingProviderSubscriptionSnapshot,
        snapshot_captured: Event,
        release_snapshot: Event,
    ) -> None:
        self._snapshot = snapshot
        self._snapshot_captured = snapshot_captured
        self._release_snapshot = release_snapshot

    def get_subscription_snapshot(
        self,
        provider_subscription_id: str,
    ) -> BillingProviderSubscriptionSnapshot | None:
        assert provider_subscription_id == self._snapshot.provider_subscription_id
        self._snapshot_captured.set()

        if not self._release_snapshot.wait(timeout=10):
            raise TimeoutError("Timed out waiting to release provider snapshot.")

        return self._snapshot


@dataclass(frozen=True, slots=True)
class ReconciliationFixture:
    tenant_id: UUID
    subscription_id: UUID
    provider_subscription_id: str
    current_period_start: datetime
    current_period_end: datetime


def _persist_provider_backed_subscription(
    *,
    provider: FakePaymentProvider,
) -> tuple[ReconciliationFixture, int]:
    customer = provider.create_customer(
        CreateCustomerRequest(provider_operation_key=(build_provider_operation_key(uuid4())))
    )
    provider_subscription = provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_customer_id=(customer.provider_customer_id),
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )
    plan_change = provider.change_plan(
        ChangePlanRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_subscription_id=(provider_subscription.provider_subscription_id),
            target_price_code="professional_monthly",
            effective_at=(provider_subscription.current_period_end),
        )
    )
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Concurrent Reconciliation Clinic {tenant_id.hex}"),
            )
        )
        session.add(
            BillingCustomer(
                id=billing_customer_id,
                tenant_id=tenant_id,
                provider=BillingProvider.FAKE,
                provider_customer_id=(customer.provider_customer_id),
            )
        )
        session.add(
            Subscription(
                id=subscription_id,
                tenant_id=tenant_id,
                billing_customer_id=billing_customer_id,
                provider=BillingProvider.FAKE,
                provider_subscription_id=(provider_subscription.provider_subscription_id),
                price_code="starter_monthly",
                plan=BillingPlan.STARTER,
                billing_interval=(BillingInterval.MONTHLY),
                currency="USD",
                unit_amount=4900,
                pending_price_code=("professional_monthly"),
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=False,
                cancellation_requested_at=None,
                current_period_start=(provider_subscription.current_period_start),
                current_period_end=(provider_subscription.current_period_end),
                provider_state_version=(plan_change.provider_state_version),
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return (
        ReconciliationFixture(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            provider_subscription_id=(provider_subscription.provider_subscription_id),
            current_period_start=(provider_subscription.current_period_start),
            current_period_end=(provider_subscription.current_period_end),
        ),
        plan_change.provider_state_version,
    )


def _persist_race_fixture() -> tuple[ReconciliationFixture, UUID]:
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()
    webhook_event_id = uuid4()
    provider_subscription_id = f"fake_sub_{uuid4().hex}"
    provider_event_id = f"evt_{uuid4().hex}"
    payload = {
        "id": provider_event_id,
        "type": "subscription.canceled",
        "created_at": (WEBHOOK_PROCESSED_AT.isoformat()),
        "data": {
            "provider_subscription_id": (provider_subscription_id),
            "provider_state_version": 6,
            "price_code": "starter_monthly",
            "status": "canceled",
            "current_period_start": (PERIOD_START.isoformat()),
            "current_period_end": (WEBHOOK_PROCESSED_AT.isoformat()),
            "canceled_at": (WEBHOOK_PROCESSED_AT.isoformat()),
        },
    }

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Reconciliation Webhook Race Clinic {tenant_id.hex}"),
            )
        )
        session.add(
            BillingCustomer(
                id=billing_customer_id,
                tenant_id=tenant_id,
                provider=BillingProvider.FAKE,
                provider_customer_id=(f"fake_customer_{uuid4().hex}"),
            )
        )
        session.add(
            Subscription(
                id=subscription_id,
                tenant_id=tenant_id,
                billing_customer_id=billing_customer_id,
                provider=BillingProvider.FAKE,
                provider_subscription_id=(provider_subscription_id),
                price_code="starter_monthly",
                plan=BillingPlan.STARTER,
                billing_interval=(BillingInterval.MONTHLY),
                currency="USD",
                unit_amount=4900,
                pending_price_code=None,
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=False,
                cancellation_requested_at=None,
                current_period_start=PERIOD_START,
                current_period_end=WEBHOOK_PROCESSED_AT,
                provider_state_version=4,
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.add(
            BillingWebhookEvent(
                id=webhook_event_id,
                provider=BillingProvider.FAKE,
                provider_event_id=provider_event_id,
                event_type=(BillingWebhookEventType.SUBSCRIPTION_CANCELED.value),
                provider_subscription_id=(provider_subscription_id),
                provider_created_at=(WEBHOOK_PROCESSED_AT),
                provider_state_version=6,
                payload=payload,
                payload_sha256=sha256(provider_event_id.encode("utf-8")).hexdigest(),
                signature_timestamp=int(WEBHOOK_PROCESSED_AT.timestamp()),
                status=(BillingWebhookEventStatus.RECEIVED),
                processing_attempt_count=0,
            )
        )
        session.commit()

    return (
        ReconciliationFixture(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            provider_subscription_id=(provider_subscription_id),
            current_period_start=PERIOD_START,
            current_period_end=WEBHOOK_PROCESSED_AT,
        ),
        webhook_event_id,
    )


def _reconcile_concurrently(
    *,
    barrier: Barrier,
    provider: PaymentProvider,
    subscription_id: UUID,
) -> ReconciledBillingSubscription:
    barrier.wait(timeout=10)

    with Session(get_engine()) as session:
        return ReconcileBillingSubscriptionService(
            payment_provider=provider,
        ).execute(
            session,
            ReconcileBillingSubscriptionCommand(subscription_id=subscription_id),
        )


def _run_reconciliation(
    *,
    provider: PaymentProvider,
    subscription_id: UUID,
) -> ReconciledBillingSubscription:
    with Session(get_engine()) as session:
        return ReconcileBillingSubscriptionService(
            payment_provider=provider,
        ).execute(
            session,
            ReconcileBillingSubscriptionCommand(subscription_id=subscription_id),
        )


def _process_webhook(webhook_event_id: UUID) -> None:
    with Session(get_engine()) as session:
        ProcessBillingWebhookEventService(clock=FixedWebhookClock()).execute(
            session,
            ProcessBillingWebhookEventCommand(webhook_event_id=webhook_event_id),
        )


def _load_subscription(
    subscription_id: UUID,
) -> Subscription:
    with Session(get_engine()) as session:
        subscription = session.get(
            Subscription,
            subscription_id,
        )

        assert subscription is not None
        session.expunge(subscription)
        return subscription


def _load_webhook_event(
    webhook_event_id: UUID,
) -> BillingWebhookEvent:
    with Session(get_engine()) as session:
        event = session.get(
            BillingWebhookEvent,
            webhook_event_id,
        )

        assert event is not None
        session.expunge(event)
        return event


def _cleanup(
    *,
    tenant_id: UUID,
    webhook_event_ids: tuple[UUID, ...] = (),
) -> None:
    with Session(get_engine()) as session:
        if webhook_event_ids:
            session.execute(
                delete(BillingWebhookEvent).where(BillingWebhookEvent.id.in_(webhook_event_ids))
            )

        session.execute(delete(Subscription).where(Subscription.tenant_id == tenant_id))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def test_concurrent_reconciliation_repairs_once() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    fixture, scheduled_version = _persist_provider_backed_subscription(provider=provider)
    clock.current = fixture.current_period_end
    barrier = Barrier(2)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _reconcile_concurrently,
                    barrier=barrier,
                    provider=provider,
                    subscription_id=(fixture.subscription_id),
                )
                for _ in range(2)
            ]
            results = [future.result(timeout=20) for future in futures]

        outcomes = [result.outcome for result in results]

        assert outcomes.count(BillingReconciliationOutcome.REPAIRED) == 1
        assert outcomes.count(BillingReconciliationOutcome.IN_SYNC) == 1

        repaired = next(
            result for result in results if result.outcome is BillingReconciliationOutcome.REPAIRED
        )
        in_sync = next(
            result for result in results if result.outcome is BillingReconciliationOutcome.IN_SYNC
        )
        subscription = _load_subscription(fixture.subscription_id)
        snapshot = provider.get_subscription_snapshot(fixture.provider_subscription_id)

        assert snapshot is not None
        assert repaired.previous_provider_state_version == (scheduled_version)
        assert repaired.provider_state_version == (snapshot.provider_state_version)
        assert in_sync.provider_state_version == (snapshot.provider_state_version)

        assert subscription.price_code == ("professional_monthly")
        assert subscription.plan is BillingPlan.PROFESSIONAL
        assert subscription.pending_price_code is None
        assert subscription.provider_state_version == snapshot.provider_state_version
        assert subscription.current_period_start == snapshot.current_period_start
        assert subscription.current_period_end == snapshot.current_period_end
    finally:
        _cleanup(tenant_id=fixture.tenant_id)


def test_newer_webhook_state_wins_over_captured_snapshot() -> None:
    fixture, webhook_event_id = _persist_race_fixture()
    snapshot_captured = Event()
    release_snapshot = Event()
    provider = BlockingSnapshotProvider(
        snapshot=BillingProviderSubscriptionSnapshot(
            provider=BillingProvider.FAKE,
            provider_subscription_id=(fixture.provider_subscription_id),
            provider_state_version=5,
            price_code="starter_monthly",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=(fixture.current_period_start),
            current_period_end=(fixture.current_period_end),
            cancel_at_period_end=False,
            canceled_at=None,
            observed_at=BEFORE_PERIOD_END,
        ),
        snapshot_captured=snapshot_captured,
        release_snapshot=release_snapshot,
    )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _run_reconciliation,
                provider=cast(
                    PaymentProvider,
                    provider,
                ),
                subscription_id=fixture.subscription_id,
            )

            assert snapshot_captured.wait(timeout=10)

            try:
                _process_webhook(webhook_event_id)
            finally:
                release_snapshot.set()

            result = future.result(timeout=20)

        subscription = _load_subscription(fixture.subscription_id)
        event = _load_webhook_event(webhook_event_id)

        assert result.outcome is BillingReconciliationOutcome.IGNORED
        assert result.previous_provider_state_version == 6
        assert result.provider_state_version == 5
        assert result.drift_fields == ("provider_state_version",)

        assert subscription.status is SubscriptionStatus.CANCELED
        assert subscription.canceled_at == (WEBHOOK_PROCESSED_AT)
        assert subscription.cancel_at_period_end is False
        assert subscription.provider_state_version == 6
        assert subscription.last_provider_event_at == (WEBHOOK_PROCESSED_AT)

        assert event.status is BillingWebhookEventStatus.PROCESSED
        assert event.processing_attempt_count == 1
        assert event.processed_at == WEBHOOK_PROCESSED_AT
        assert event.failure_code is None
    finally:
        release_snapshot.set()
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(webhook_event_id,),
        )
