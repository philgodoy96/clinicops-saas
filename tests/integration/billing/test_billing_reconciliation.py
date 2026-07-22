from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    BillingReconciliationOutcome,
    SubscriptionStatus,
)
from clinicops.billing.models import (
    BillingCustomer,
    Subscription,
)
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
from clinicops.billing.reconciliation import (
    ReconcileBillingSubscriptionCommand,
    ReconcileBillingSubscriptionService,
    ReconciledBillingSubscription,
)
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
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
CANCELLATION_REQUESTED_AT = datetime(
    2026,
    7,
    24,
    15,
    tzinfo=UTC,
)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


class FailAfterFlushSubscriptionRepository(SubscriptionRepository):
    """Simulate failure after dirty state reaches PostgreSQL."""

    def flush(self, session: Session) -> None:
        super().flush(session)
        raise RuntimeError("Simulated reconciliation persistence failure.")


@dataclass(frozen=True, slots=True)
class ProviderSubscriptionState:
    provider_customer_id: str
    provider_subscription_id: str
    current_period_start: datetime
    current_period_end: datetime
    provider_state_version: int


@dataclass(frozen=True, slots=True)
class PersistedSubscriptionFixture:
    tenant_id: UUID
    subscription_id: UUID
    provider_subscription_id: str


def _create_provider_subscription(
    provider: FakePaymentProvider,
) -> ProviderSubscriptionState:
    customer = provider.create_customer(
        CreateCustomerRequest(provider_operation_key=(build_provider_operation_key(uuid4())))
    )
    subscription = provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_customer_id=(customer.provider_customer_id),
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )

    return ProviderSubscriptionState(
        provider_customer_id=(customer.provider_customer_id),
        provider_subscription_id=(subscription.provider_subscription_id),
        current_period_start=(subscription.current_period_start),
        current_period_end=(subscription.current_period_end),
        provider_state_version=(subscription.provider_state_version),
    )


def _persist_local_subscription(
    *,
    provider_state: ProviderSubscriptionState,
    provider_state_version: int,
    pending_price_code: str | None = None,
    cancel_at_period_end: bool = False,
    cancellation_requested_at: datetime | None = None,
) -> PersistedSubscriptionFixture:
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Reconciliation Clinic {tenant_id.hex}"),
            )
        )
        session.add(
            BillingCustomer(
                id=billing_customer_id,
                tenant_id=tenant_id,
                provider=BillingProvider.FAKE,
                provider_customer_id=(provider_state.provider_customer_id),
            )
        )
        session.add(
            Subscription(
                id=subscription_id,
                tenant_id=tenant_id,
                billing_customer_id=billing_customer_id,
                provider=BillingProvider.FAKE,
                provider_subscription_id=(provider_state.provider_subscription_id),
                price_code="starter_monthly",
                plan=BillingPlan.STARTER,
                billing_interval=(BillingInterval.MONTHLY),
                currency="USD",
                unit_amount=4900,
                pending_price_code=(pending_price_code),
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=(cancel_at_period_end),
                cancellation_requested_at=(cancellation_requested_at),
                current_period_start=(provider_state.current_period_start),
                current_period_end=(provider_state.current_period_end),
                provider_state_version=(provider_state_version),
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return PersistedSubscriptionFixture(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        provider_subscription_id=(provider_state.provider_subscription_id),
    )


def _execute(
    *,
    provider: FakePaymentProvider,
    subscription_id: UUID,
    repository: SubscriptionRepository | None = None,
) -> ReconciledBillingSubscription:
    service = ReconcileBillingSubscriptionService(
        payment_provider=provider,
        subscription_repository=repository,
    )

    with Session(get_engine()) as session:
        return service.execute(
            session,
            ReconcileBillingSubscriptionCommand(subscription_id=subscription_id),
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


def _cleanup_tenants(*tenant_ids: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(Subscription).where(Subscription.tenant_id.in_(tenant_ids)))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id.in_(tenant_ids)))
        session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        session.commit()


def test_reconciliation_repairs_materialized_plan_change() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_state = _create_provider_subscription(provider)
    plan_change = provider.change_plan(
        ChangePlanRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_subscription_id=(provider_state.provider_subscription_id),
            target_price_code="professional_monthly",
            effective_at=(provider_state.current_period_end),
        )
    )
    fixture = _persist_local_subscription(
        provider_state=provider_state,
        provider_state_version=(plan_change.provider_state_version),
        pending_price_code="professional_monthly",
    )
    clock.current = provider_state.current_period_end

    try:
        result = _execute(
            provider=provider,
            subscription_id=fixture.subscription_id,
        )
        subscription = _load_subscription(fixture.subscription_id)
        snapshot = provider.get_subscription_snapshot(fixture.provider_subscription_id)

        assert snapshot is not None
        assert result.outcome is BillingReconciliationOutcome.REPAIRED
        assert result.previous_provider_state_version == (plan_change.provider_state_version)
        assert result.provider_state_version == (snapshot.provider_state_version)

        assert subscription.price_code == ("professional_monthly")
        assert subscription.plan is BillingPlan.PROFESSIONAL
        assert subscription.billing_interval is BillingInterval.MONTHLY
        assert subscription.currency == "USD"
        assert subscription.unit_amount == 9900
        assert subscription.pending_price_code is None
        assert subscription.status is SubscriptionStatus.ACTIVE
        assert subscription.current_period_start == snapshot.current_period_start
        assert subscription.current_period_end == snapshot.current_period_end
        assert subscription.provider_state_version == snapshot.provider_state_version
        assert subscription.last_provider_event_at == snapshot.observed_at

        replay = _execute(
            provider=provider,
            subscription_id=fixture.subscription_id,
        )

        assert replay.outcome is BillingReconciliationOutcome.IN_SYNC
        assert replay.drift_fields == ()
    finally:
        _cleanup_tenants(fixture.tenant_id)


def test_reconciliation_repairs_final_cancellation() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_state = _create_provider_subscription(provider)
    cancellation = provider.cancel_subscription(
        CancelSubscriptionRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_subscription_id=(provider_state.provider_subscription_id),
            effective_at=(provider_state.current_period_end),
        )
    )
    fixture = _persist_local_subscription(
        provider_state=provider_state,
        provider_state_version=(cancellation.provider_state_version),
        cancel_at_period_end=True,
        cancellation_requested_at=(CANCELLATION_REQUESTED_AT),
    )
    clock.current = provider_state.current_period_end

    try:
        result = _execute(
            provider=provider,
            subscription_id=fixture.subscription_id,
        )
        subscription = _load_subscription(fixture.subscription_id)
        snapshot = provider.get_subscription_snapshot(fixture.provider_subscription_id)

        assert snapshot is not None
        assert snapshot.status is SubscriptionStatus.CANCELED
        assert result.outcome is BillingReconciliationOutcome.REPAIRED
        assert subscription.status is SubscriptionStatus.CANCELED
        assert subscription.canceled_at == (provider_state.current_period_end)
        assert subscription.cancel_at_period_end is False
        assert subscription.cancellation_requested_at == (CANCELLATION_REQUESTED_AT)
        assert subscription.pending_price_code is None
        assert subscription.provider_state_version == snapshot.provider_state_version
        assert subscription.last_provider_event_at == snapshot.observed_at
    finally:
        _cleanup_tenants(fixture.tenant_id)


def test_older_provider_snapshot_is_ignored() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_state = _create_provider_subscription(provider)
    fixture = _persist_local_subscription(
        provider_state=provider_state,
        provider_state_version=(provider_state.provider_state_version + 1),
    )

    try:
        result = _execute(
            provider=provider,
            subscription_id=fixture.subscription_id,
        )
        subscription = _load_subscription(fixture.subscription_id)

        assert result.outcome is BillingReconciliationOutcome.IGNORED
        assert result.drift_fields == ("provider_state_version",)
        assert subscription.provider_state_version == (provider_state.provider_state_version + 1)
        assert subscription.price_code == "starter_monthly"
        assert subscription.current_period_start == (provider_state.current_period_start)
        assert subscription.current_period_end == (provider_state.current_period_end)
        assert subscription.last_provider_event_at is None
    finally:
        _cleanup_tenants(fixture.tenant_id)


def test_reconciliation_failure_rolls_back_local_repair() -> None:
    clock = MutableClock(BEFORE_PERIOD_END)
    provider = FakePaymentProvider(clock=clock)
    provider_state = _create_provider_subscription(provider)
    plan_change = provider.change_plan(
        ChangePlanRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_subscription_id=(provider_state.provider_subscription_id),
            target_price_code="professional_monthly",
            effective_at=(provider_state.current_period_end),
        )
    )
    fixture = _persist_local_subscription(
        provider_state=provider_state,
        provider_state_version=(plan_change.provider_state_version),
        pending_price_code="professional_monthly",
    )
    clock.current = provider_state.current_period_end

    try:
        with pytest.raises(
            RuntimeError,
            match="Simulated reconciliation",
        ):
            _execute(
                provider=provider,
                subscription_id=fixture.subscription_id,
                repository=(FailAfterFlushSubscriptionRepository()),
            )

        subscription = _load_subscription(fixture.subscription_id)

        assert subscription.price_code == "starter_monthly"
        assert subscription.plan is BillingPlan.STARTER
        assert subscription.unit_amount == 4900
        assert subscription.pending_price_code == ("professional_monthly")
        assert subscription.current_period_start == (provider_state.current_period_start)
        assert subscription.current_period_end == (provider_state.current_period_end)
        assert subscription.provider_state_version == (plan_change.provider_state_version)
        assert subscription.last_provider_event_at is None
    finally:
        _cleanup_tenants(fixture.tenant_id)
