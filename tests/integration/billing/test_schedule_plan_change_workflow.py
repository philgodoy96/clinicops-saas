from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
)
from clinicops.billing.models import (
    BillingCustomer,
    ProviderOperation,
    Subscription,
)
from clinicops.billing.providers.contracts import (
    CreateCustomerRequest,
    CreateSubscriptionRequest,
)
from clinicops.billing.providers.control import (
    FakeProviderControl,
    FakeProviderOutcome,
)
from clinicops.billing.providers.exceptions import (
    ProviderAmbiguousOutcomeError,
    ProviderTerminalError,
)
from clinicops.billing.providers.fake import (
    FakePaymentProvider,
)
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
)
from clinicops.billing.services.schedule_plan_change import (
    ScheduleBillingPlanChangeCommand,
    ScheduleBillingPlanChangeService,
    ScheduledBillingPlanChange,
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
FIXED_NOW = datetime(
    2026,
    7,
    22,
    13,
    tzinfo=UTC,
)


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


@dataclass(frozen=True, slots=True)
class PersistedPlanChangeFixture:
    tenant_id: UUID
    subscription_id: UUID
    provider: FakePaymentProvider
    provider_subscription_id: str
    current_period_start: datetime
    current_period_end: datetime


@pytest.fixture
def plan_change_fixture() -> Iterator[PersistedPlanChangeFixture]:
    fixture = _persist_subscription()

    try:
        yield fixture
    finally:
        _cleanup_tenants(fixture.tenant_id)


def _persist_subscription(
    *,
    provider: FakePaymentProvider | None = None,
) -> PersistedPlanChangeFixture:
    resolved_provider = provider or FakePaymentProvider()
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()

    provider_customer = resolved_provider.create_customer(
        CreateCustomerRequest(provider_operation_key=(build_provider_operation_key(uuid4())))
    )
    provider_subscription = resolved_provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_customer_id=(provider_customer.provider_customer_id),
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Plan Change Clinic {tenant_id.hex}"),
            )
        )
        session.add(
            BillingCustomer(
                id=billing_customer_id,
                tenant_id=tenant_id,
                provider=BillingProvider.FAKE,
                provider_customer_id=(provider_customer.provider_customer_id),
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
                pending_price_code=None,
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=False,
                cancellation_requested_at=None,
                current_period_start=(provider_subscription.current_period_start),
                current_period_end=(provider_subscription.current_period_end),
                provider_state_version=(provider_subscription.provider_state_version),
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return PersistedPlanChangeFixture(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        provider=resolved_provider,
        provider_subscription_id=(provider_subscription.provider_subscription_id),
        current_period_start=(provider_subscription.current_period_start),
        current_period_end=(provider_subscription.current_period_end),
    )


def _service(
    provider: FakePaymentProvider,
) -> ScheduleBillingPlanChangeService:
    return ScheduleBillingPlanChangeService(
        payment_provider=provider,
        clock=FixedClock(),
    )


def _command(
    fixture: PersistedPlanChangeFixture,
    *,
    target_price_code: str = "professional_monthly",
    idempotency_key: str = "plan-change-request-1",
) -> ScheduleBillingPlanChangeCommand:
    return ScheduleBillingPlanChangeCommand(
        tenant_id=fixture.tenant_id,
        target_price_code=target_price_code,
        idempotency_key=idempotency_key,
    )


def _execute(
    service: ScheduleBillingPlanChangeService,
    command: ScheduleBillingPlanChangeCommand,
) -> ScheduledBillingPlanChange:
    with Session(get_engine()) as session:
        return service.execute(
            session,
            command,
        )


def _load_subscription(
    tenant_id: UUID,
) -> Subscription:
    with Session(get_engine()) as session:
        subscription = session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )

        assert subscription is not None
        return subscription


def _load_operations(
    tenant_id: UUID,
) -> list[ProviderOperation]:
    with Session(get_engine()) as session:
        return list(
            session.scalars(
                select(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id)
            ).all()
        )


def _cleanup_tenants(*tenant_ids: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(
            delete(ProviderOperation).where(ProviderOperation.tenant_id.in_(tenant_ids))
        )
        session.execute(delete(Subscription).where(Subscription.tenant_id.in_(tenant_ids)))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id.in_(tenant_ids)))
        session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        session.commit()


def test_plan_change_persists_pending_target_and_operation(
    plan_change_fixture: PersistedPlanChangeFixture,
) -> None:
    service = _service(plan_change_fixture.provider)

    result = _execute(
        service,
        _command(plan_change_fixture),
    )

    subscription = _load_subscription(plan_change_fixture.tenant_id)
    operations = _load_operations(plan_change_fixture.tenant_id)

    assert result.replayed is False
    assert result.price_code == "starter_monthly"
    assert result.pending_price_code == "professional_monthly"
    assert subscription.id == (plan_change_fixture.subscription_id)
    assert subscription.price_code == "starter_monthly"
    assert subscription.pending_price_code == "professional_monthly"
    assert subscription.plan is BillingPlan.STARTER
    assert subscription.billing_interval is BillingInterval.MONTHLY
    assert subscription.unit_amount == 4900
    assert subscription.current_period_start == plan_change_fixture.current_period_start
    assert subscription.current_period_end == plan_change_fixture.current_period_end
    assert subscription.provider_state_version == 2

    assert len(operations) == 1
    operation = operations[0]

    assert operation.operation_type is ProviderOperationType.CHANGE_PLAN
    assert operation.status is ProviderOperationStatus.SUCCEEDED
    assert operation.attempt_count == 1
    assert operation.subscription_id == plan_change_fixture.subscription_id
    assert operation.result_payload is not None
    assert operation.result_payload["effective_price_code"] == "professional_monthly"


def test_successful_plan_change_replays_without_duplicate_operation(
    plan_change_fixture: PersistedPlanChangeFixture,
) -> None:
    service = _service(plan_change_fixture.provider)
    command = _command(
        plan_change_fixture,
        idempotency_key="plan-change-replay",
    )

    first = _execute(service, command)
    replayed = _execute(service, command)

    assert replayed.id == first.id
    assert replayed.replayed is True
    assert replayed.pending_price_code == "professional_monthly"

    operations = _load_operations(plan_change_fixture.tenant_id)

    assert len(operations) == 1
    assert operations[0].attempt_count == 1
    assert operations[0].status is ProviderOperationStatus.SUCCEEDED


def test_ambiguous_plan_change_recovers_with_same_provider_key() -> None:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)
    fixture = _persist_subscription(provider=provider)
    service = _service(provider)
    command = _command(
        fixture,
        idempotency_key="ambiguous-plan-change",
    )
    control.queue_outcome(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        outcome=FakeProviderOutcome.AMBIGUOUS_SUCCESS,
    )

    try:
        with pytest.raises(ProviderAmbiguousOutcomeError):
            _execute(service, command)

        failed_operation = _load_operations(fixture.tenant_id)[0]
        failed_subscription = _load_subscription(fixture.tenant_id)

        assert failed_operation.status is ProviderOperationStatus.FAILED_RETRYABLE
        assert failed_operation.attempt_count == 1
        assert failed_subscription.pending_price_code is None

        recovered = _execute(service, command)
        recovered_operation = _load_operations(fixture.tenant_id)[0]

        assert recovered.replayed is False
        assert recovered.pending_price_code == "professional_monthly"
        assert recovered_operation.attempt_count == 2
        assert recovered_operation.status is ProviderOperationStatus.SUCCEEDED
        assert _load_subscription(fixture.tenant_id).provider_state_version == 2
    finally:
        _cleanup_tenants(fixture.tenant_id)


def test_terminal_plan_change_failure_is_persisted_and_not_retried() -> None:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)
    fixture = _persist_subscription(provider=provider)
    service = _service(provider)
    command = _command(
        fixture,
        idempotency_key="terminal-plan-change",
    )
    control.queue_outcome(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        outcome=FakeProviderOutcome.TERMINAL_REJECTION,
    )

    try:
        with pytest.raises(ProviderTerminalError):
            _execute(service, command)

        first_operation = _load_operations(fixture.tenant_id)[0]

        assert first_operation.status is ProviderOperationStatus.FAILED_TERMINAL
        assert first_operation.attempt_count == 1
        assert _load_subscription(fixture.tenant_id).pending_price_code is None

        with pytest.raises(ProviderTerminalError):
            _execute(service, command)

        replayed_operation = _load_operations(fixture.tenant_id)[0]

        assert replayed_operation.attempt_count == 1
        assert replayed_operation.status is ProviderOperationStatus.FAILED_TERMINAL
    finally:
        _cleanup_tenants(fixture.tenant_id)
