from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.models import AuditLogEntry
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
    BillingSubscriptionCancellationPendingError,
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
from clinicops.billing.services.schedule_cancellation import (
    ScheduleBillingSubscriptionCancellationCommand,
    ScheduleBillingSubscriptionCancellationService,
)
from clinicops.db.session import get_engine
from clinicops.identity.models import User, UserStatus
from clinicops.tenancy.models import Tenant, TenantRole

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
    24,
    15,
    tzinfo=UTC,
)


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


def _audit_context(*, user_id: UUID) -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=user_id,
        role=TenantRole.OWNER.value,
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
    )


def _create_user(prefix: str) -> User:
    return User(
        email=f"{prefix}-{uuid4()}@example.com",
        status=UserStatus.ACTIVE,
    )


def _persist_user(user: User) -> UUID:
    with Session(get_engine()) as session:
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    return user_id


def _delete_user(user_id: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


@dataclass(frozen=True, slots=True)
class ConcurrentCancellationFixture:
    tenant_id: UUID
    audit_user_id: UUID
    provider: FakePaymentProvider


def _persist_fixture() -> ConcurrentCancellationFixture:
    provider = FakePaymentProvider()
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    audit_user_id = _persist_user(_create_user("cancellation-concurrency-audit"))

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
                name=(f"Concurrent Cancellation Clinic {tenant_id.hex}"),
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

    return ConcurrentCancellationFixture(
        tenant_id=tenant_id,
        audit_user_id=audit_user_id,
        provider=provider,
    )


def _cleanup_tenant(tenant_id: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id))
        session.execute(delete(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id))
        session.execute(delete(Subscription).where(Subscription.tenant_id == tenant_id))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def _execute_concurrently(
    *,
    service: ScheduleBillingSubscriptionCancellationService,
    barrier: Barrier,
    tenant_id: UUID,
    audit_user_id: UUID,
    idempotency_key: str,
) -> str:
    barrier.wait(timeout=10)

    with Session(get_engine()) as session:
        try:
            result = service.execute(
                session,
                ScheduleBillingSubscriptionCancellationCommand(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    audit_context=_audit_context(user_id=audit_user_id),
                ),
            )
        except ProviderOperationInProgressError:
            return "in_progress"
        except BillingIdempotencyConflictError:
            return "idempotency_conflict"
        except BillingSubscriptionCancellationPendingError:
            return "pending_conflict"
        except ProviderTerminalError:
            return "provider_terminal"

        session.commit()
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


def test_concurrent_same_key_schedules_one_logical_cancellation() -> None:
    fixture = _persist_fixture()

    try:
        service = ScheduleBillingSubscriptionCancellationService(
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
                    audit_user_id=fixture.audit_user_id,
                    idempotency_key=("same-concurrent-cancellation"),
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
        assert subscription.status is SubscriptionStatus.ACTIVE
        assert subscription.cancel_at_period_end is True
        assert subscription.cancellation_requested_at == FIXED_NOW
        assert subscription.canceled_at is None
        assert subscription.pending_price_code is None
        assert subscription.provider_state_version == 2
        assert len(operations) == 1
        assert operations[0].operation_type is ProviderOperationType.CANCEL_SUBSCRIPTION
        assert operations[0].status is ProviderOperationStatus.SUCCEEDED
        assert operations[0].attempt_count == 1
    finally:
        _cleanup_tenant(fixture.tenant_id)
        _delete_user(fixture.audit_user_id)


def test_concurrent_different_keys_preserve_one_cancellation() -> None:
    fixture = _persist_fixture()

    try:
        service = ScheduleBillingSubscriptionCancellationService(
            payment_provider=fixture.provider,
            clock=FixedClock(),
        )
        barrier = Barrier(2)
        keys = (
            "different-cancellation-key-a",
            "different-cancellation-key-b",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _execute_concurrently,
                    service=service,
                    barrier=barrier,
                    tenant_id=fixture.tenant_id,
                    audit_user_id=fixture.audit_user_id,
                    idempotency_key=idempotency_key,
                )
                for idempotency_key in keys
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
        assert subscription.status is SubscriptionStatus.ACTIVE
        assert subscription.cancel_at_period_end is True
        assert subscription.cancellation_requested_at == FIXED_NOW
        assert subscription.canceled_at is None
        assert subscription.provider_state_version == 2
        assert len(operations) in {1, 2}
        assert (
            sum(operation.status is ProviderOperationStatus.SUCCEEDED for operation in operations)
            == 1
        )
        assert sum(
            operation.status is ProviderOperationStatus.FAILED_TERMINAL for operation in operations
        ) in {0, 1}
    finally:
        _cleanup_tenant(fixture.tenant_id)
        _delete_user(fixture.audit_user_id)
