from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

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
from clinicops.billing.exceptions import (
    BillingIdempotencyConflictError,
    BillingPlanChangeAlreadyPendingError,
    ProviderOperationInProgressError,
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
from clinicops.billing.providers.exceptions import (
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
class ConcurrentPlanChangeFixture:
    tenant_id: UUID
    provider: FakePaymentProvider


def _persist_fixture() -> ConcurrentPlanChangeFixture:
    provider = FakePaymentProvider()
    tenant_id = uuid4()
    billing_customer_id = uuid4()

    provider_customer = provider.create_customer(
        CreateCustomerRequest(provider_operation_key=(build_provider_operation_key(uuid4())))
    )
    provider_subscription = provider.create_subscription(
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
                name=(f"Concurrent Plan Clinic {tenant_id.hex}"),
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
                id=uuid4(),
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
                provider_state_version=1,
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return ConcurrentPlanChangeFixture(
        tenant_id=tenant_id,
        provider=provider,
    )


def _cleanup_tenant(tenant_id: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id))
        session.execute(delete(Subscription).where(Subscription.tenant_id == tenant_id))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def _execute_concurrently(
    *,
    service: ScheduleBillingPlanChangeService,
    barrier: Barrier,
    tenant_id: UUID,
    idempotency_key: str,
    target_price_code: str,
) -> str:
    barrier.wait(timeout=10)

    with Session(get_engine()) as session:
        try:
            result = service.execute(
                session,
                ScheduleBillingPlanChangeCommand(
                    tenant_id=tenant_id,
                    target_price_code=target_price_code,
                    idempotency_key=idempotency_key,
                ),
            )
        except ProviderOperationInProgressError:
            return "in_progress"
        except BillingIdempotencyConflictError:
            return "idempotency_conflict"
        except BillingPlanChangeAlreadyPendingError:
            return "pending_conflict"
        except ProviderTerminalError:
            return "provider_terminal"

    return "replayed" if result.replayed else "scheduled"


def _load_state(
    tenant_id: UUID,
) -> tuple[Subscription, list[ProviderOperation]]:
    with Session(get_engine()) as session:
        subscription = session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        operations = list(
            session.scalars(
                select(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id)
            ).all()
        )

        assert subscription is not None
        return subscription, operations


def test_concurrent_same_key_schedules_one_logical_change() -> None:
    fixture = _persist_fixture()

    try:
        service = ScheduleBillingPlanChangeService(
            payment_provider=fixture.provider,
            clock=FixedClock(),
        )
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _execute_concurrently,
                    service=service,
                    barrier=barrier,
                    tenant_id=fixture.tenant_id,
                    idempotency_key=("same-concurrent-plan-change"),
                    target_price_code=("professional_monthly"),
                )
                for _ in range(2)
            ]
            outcomes = [future.result(timeout=20) for future in futures]

        subscription, operations = _load_state(fixture.tenant_id)

        assert "scheduled" in outcomes
        assert set(outcomes).issubset(
            {
                "scheduled",
                "replayed",
                "in_progress",
            }
        )
        assert subscription.price_code == ("starter_monthly")
        assert subscription.pending_price_code == "professional_monthly"
        assert subscription.provider_state_version == 2
        assert len(operations) == 1
        assert operations[0].operation_type is ProviderOperationType.CHANGE_PLAN
        assert operations[0].status is ProviderOperationStatus.SUCCEEDED
        assert operations[0].attempt_count == 1
    finally:
        _cleanup_tenant(fixture.tenant_id)


def test_concurrent_different_keys_preserve_one_pending_target() -> None:
    fixture = _persist_fixture()

    try:
        service = ScheduleBillingPlanChangeService(
            payment_provider=fixture.provider,
            clock=FixedClock(),
        )
        barrier = Barrier(2)
        requests = (
            (
                "different-plan-change-key-a",
                "professional_monthly",
            ),
            (
                "different-plan-change-key-b",
                "starter_yearly",
            ),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _execute_concurrently,
                    service=service,
                    barrier=barrier,
                    tenant_id=fixture.tenant_id,
                    idempotency_key=idempotency_key,
                    target_price_code=target_price_code,
                )
                for (
                    idempotency_key,
                    target_price_code,
                ) in requests
            ]
            outcomes = [future.result(timeout=20) for future in futures]

        subscription, operations = _load_state(fixture.tenant_id)

        assert outcomes.count("scheduled") == 1
        assert any(
            outcome
            in {
                "pending_conflict",
                "provider_terminal",
            }
            for outcome in outcomes
        )
        assert subscription.price_code == ("starter_monthly")
        assert subscription.pending_price_code in {
            "professional_monthly",
            "starter_yearly",
        }
        assert subscription.provider_state_version == 2
        assert len(operations) in {1, 2}
        assert (
            sum(operation.status is ProviderOperationStatus.SUCCEEDED for operation in operations)
            == 1
        )
    finally:
        _cleanup_tenant(fixture.tenant_id)


def test_concurrent_same_key_with_different_targets_conflicts() -> None:
    fixture = _persist_fixture()

    try:
        service = ScheduleBillingPlanChangeService(
            payment_provider=fixture.provider,
            clock=FixedClock(),
        )
        barrier = Barrier(2)
        targets = (
            "professional_monthly",
            "starter_yearly",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _execute_concurrently,
                    service=service,
                    barrier=barrier,
                    tenant_id=fixture.tenant_id,
                    idempotency_key=("same-key-different-target"),
                    target_price_code=target,
                )
                for target in targets
            ]
            outcomes = [future.result(timeout=20) for future in futures]

        subscription, operations = _load_state(fixture.tenant_id)

        assert outcomes.count("scheduled") == 1
        assert "idempotency_conflict" in outcomes
        assert subscription.pending_price_code in set(targets)
        assert len(operations) == 1
        assert operations[0].status is ProviderOperationStatus.SUCCEEDED
    finally:
        _cleanup_tenant(fixture.tenant_id)
