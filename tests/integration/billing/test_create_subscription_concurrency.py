from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.billing.exceptions import (
    BillingCustomerAlreadyExistsError,
    BillingSubscriptionAlreadyExistsError,
    ProviderOperationInProgressError,
)
from clinicops.billing.models import (
    BillingCustomer,
    ProviderOperation,
    Subscription,
)
from clinicops.billing.providers.exceptions import (
    ProviderTerminalError,
)
from clinicops.billing.providers.fake import (
    FakePaymentProvider,
)
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionCommand,
    CreateBillingSubscriptionService,
)
from clinicops.db.session import get_engine
from clinicops.tenancy.models import Tenant

FIXED_NOW = datetime(
    2026,
    7,
    22,
    12,
    tzinfo=UTC,
)


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


def _persist_tenant() -> UUID:
    tenant_id = uuid4()

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Billing Concurrency Clinic {tenant_id.hex}"),
            )
        )
        session.commit()

    return tenant_id


def _cleanup_tenant(tenant_id: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id))
        session.execute(delete(Subscription).where(Subscription.tenant_id == tenant_id))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def _execute_concurrently(
    *,
    service: CreateBillingSubscriptionService,
    barrier: Barrier,
    tenant_id: UUID,
    idempotency_key: str,
) -> str:
    with Session(get_engine()) as session:
        barrier.wait(timeout=10)

        try:
            result = service.execute(
                session,
                CreateBillingSubscriptionCommand(
                    tenant_id=tenant_id,
                    price_code="starter_monthly",
                    idempotency_key=idempotency_key,
                ),
            )
        except ProviderOperationInProgressError:
            session.rollback()
            return "in_progress"
        except (
            BillingCustomerAlreadyExistsError,
            BillingSubscriptionAlreadyExistsError,
            ProviderTerminalError,
        ):
            session.rollback()
            return "conflict"

        return "replayed" if result.replayed else "created"


def _count_rows(
    tenant_id: UUID,
) -> tuple[int, int, int]:
    with Session(get_engine()) as session:
        customer_count = session.scalar(
            select(func.count())
            .select_from(BillingCustomer)
            .where(BillingCustomer.tenant_id == tenant_id)
        )
        subscription_count = session.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.tenant_id == tenant_id)
        )
        operation_count = session.scalar(
            select(func.count())
            .select_from(ProviderOperation)
            .where(ProviderOperation.tenant_id == tenant_id)
        )

        return (
            int(customer_count or 0),
            int(subscription_count or 0),
            int(operation_count or 0),
        )


def test_concurrent_same_key_requests_create_one_subscription() -> None:
    tenant_id = _persist_tenant()

    try:
        service = CreateBillingSubscriptionService(
            payment_provider=FakePaymentProvider(),
            clock=FixedClock(),
        )
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _execute_concurrently,
                    service=service,
                    barrier=barrier,
                    tenant_id=tenant_id,
                    idempotency_key=("same-concurrent-request"),
                )
                for _ in range(2)
            ]
            outcomes = [future.result(timeout=20) for future in futures]

        assert "created" in outcomes
        assert set(outcomes).issubset(
            {
                "created",
                "replayed",
                "in_progress",
            }
        )
        assert _count_rows(tenant_id) == (
            1,
            1,
            2,
        )
    finally:
        _cleanup_tenant(tenant_id)


def test_concurrent_different_keys_preserve_local_uniqueness() -> None:
    tenant_id = _persist_tenant()

    try:
        service = CreateBillingSubscriptionService(
            payment_provider=FakePaymentProvider(),
            clock=FixedClock(),
        )
        barrier = Barrier(2)
        keys = (
            "different-concurrent-request-a",
            "different-concurrent-request-b",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _execute_concurrently,
                    service=service,
                    barrier=barrier,
                    tenant_id=tenant_id,
                    idempotency_key=key,
                )
                for key in keys
            ]
            outcomes = [future.result(timeout=20) for future in futures]

        assert outcomes.count("created") == 1
        assert any(
            outcome
            in {
                "conflict",
                "in_progress",
            }
            for outcome in outcomes
        )

        customer_count, subscription_count, operation_count = _count_rows(tenant_id)

        assert customer_count == 1
        assert subscription_count == 1
        assert operation_count >= 2
    finally:
        _cleanup_tenant(tenant_id)
