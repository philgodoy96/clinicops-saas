from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.models import AuditLogEntry
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
from clinicops.identity.models import User, UserStatus
from clinicops.tenancy.models import Tenant, TenantRole

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


def _persist_tenant() -> tuple[UUID, UUID]:
    tenant_id = uuid4()
    audit_user_id = _persist_user(_create_user("billing-concurrency-audit"))

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Billing Concurrency Clinic {tenant_id.hex}"),
            )
        )
        session.commit()

    return tenant_id, audit_user_id


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
    service: CreateBillingSubscriptionService,
    barrier: Barrier,
    tenant_id: UUID,
    audit_user_id: UUID,
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
                    audit_context=_audit_context(user_id=audit_user_id),
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

        session.commit()
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
    tenant_id, audit_user_id = _persist_tenant()

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
                    audit_user_id=audit_user_id,
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
        _delete_user(audit_user_id)


def test_concurrent_different_keys_preserve_local_uniqueness() -> None:
    tenant_id, audit_user_id = _persist_tenant()

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
                    audit_user_id=audit_user_id,
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
        _delete_user(audit_user_id)
