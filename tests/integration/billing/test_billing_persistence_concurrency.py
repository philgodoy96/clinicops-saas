from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    ProviderOperationType,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingCustomerAlreadyExistsError,
    BillingSubscriptionAlreadyExistsError,
    BillingWebhookEventAlreadyExistsError,
    ProviderOperationAlreadyExistsError,
)
from clinicops.billing.models import (
    BillingCustomer,
    BillingWebhookEvent,
    ProviderOperation,
    Subscription,
)
from clinicops.billing.repositories import (
    BillingCustomerRepository,
    BillingWebhookEventRepository,
    ProviderOperationRepository,
    SubscriptionRepository,
)
from clinicops.db.session import get_engine
from clinicops.tenancy.models import Tenant


def _create_committed_tenant() -> UUID:
    with Session(get_engine()) as session:
        tenant = Tenant(name=f"Billing Concurrency {uuid4().hex}")
        session.add(tenant)
        session.flush()

        tenant_id = tenant.id
        session.commit()

        return tenant_id


def _create_committed_customer(
    *,
    tenant_id: UUID,
) -> UUID:
    with Session(get_engine()) as session:
        customer = BillingCustomer(
            tenant_id=tenant_id,
            provider=BillingProvider.FAKE,
        )
        session.add(customer)
        session.flush()

        customer_id = customer.id
        session.commit()

        return customer_id


def _create_committed_subscription(
    *,
    tenant_id: UUID,
    billing_customer_id: UUID,
) -> UUID:
    with Session(get_engine()) as session:
        subscription = Subscription(
            tenant_id=tenant_id,
            billing_customer_id=billing_customer_id,
            provider=BillingProvider.FAKE,
            price_code="starter_monthly",
            plan=BillingPlan.STARTER,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=4_900,
            status=SubscriptionStatus.PENDING,
        )
        session.add(subscription)
        session.flush()

        subscription_id = subscription.id
        session.commit()

        return subscription_id


def _cleanup_tenant(
    *,
    tenant_id: UUID,
) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def _cleanup_webhook_event(
    *,
    provider_event_id: str,
) -> None:
    with Session(get_engine()) as session:
        session.execute(
            delete(BillingWebhookEvent).where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id == provider_event_id,
            )
        )
        session.commit()


def _run_two_workers(
    action: Callable[[], str],
) -> list[str]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(action) for _ in range(2)]

        return [future.result(timeout=10) for future in futures]


def test_concurrent_billing_customer_creation_preserves_uniqueness() -> None:
    tenant_id = _create_committed_tenant()
    barrier = Barrier(2)

    def create_customer() -> str:
        with Session(get_engine()) as session:
            repository = BillingCustomerRepository()
            barrier.wait(timeout=5)

            try:
                repository.add_and_flush(
                    session,
                    BillingCustomer(
                        tenant_id=tenant_id,
                        provider=BillingProvider.FAKE,
                    ),
                )
                session.commit()
            except BillingCustomerAlreadyExistsError:
                session.rollback()
                return "conflict"

            return "created"

    try:
        outcomes = _run_two_workers(create_customer)

        assert sorted(outcomes) == [
            "conflict",
            "created",
        ]

        with Session(get_engine()) as session:
            customer_ids = session.scalars(
                select(BillingCustomer.id).where(
                    BillingCustomer.tenant_id == tenant_id,
                    BillingCustomer.provider == BillingProvider.FAKE,
                )
            ).all()

        assert len(customer_ids) == 1
    finally:
        _cleanup_tenant(tenant_id=tenant_id)


def test_concurrent_subscription_creation_preserves_one_per_tenant() -> None:
    tenant_id = _create_committed_tenant()
    customer_id = _create_committed_customer(tenant_id=tenant_id)
    barrier = Barrier(2)

    def create_subscription() -> str:
        with Session(get_engine()) as session:
            repository = SubscriptionRepository()
            barrier.wait(timeout=5)

            try:
                repository.add_and_flush(
                    session,
                    Subscription(
                        tenant_id=tenant_id,
                        billing_customer_id=customer_id,
                        provider=BillingProvider.FAKE,
                        price_code="starter_monthly",
                        plan=BillingPlan.STARTER,
                        billing_interval=(BillingInterval.MONTHLY),
                        currency="USD",
                        unit_amount=4_900,
                        status=SubscriptionStatus.PENDING,
                    ),
                )
                session.commit()
            except BillingSubscriptionAlreadyExistsError:
                session.rollback()
                return "conflict"

            return "created"

    try:
        outcomes = _run_two_workers(create_subscription)

        assert sorted(outcomes) == [
            "conflict",
            "created",
        ]

        with Session(get_engine()) as session:
            subscription_ids = session.scalars(
                select(Subscription.id).where(Subscription.tenant_id == tenant_id)
            ).all()

        assert len(subscription_ids) == 1
    finally:
        _cleanup_tenant(tenant_id=tenant_id)


def test_concurrent_provider_operations_preserve_idempotency_scope() -> None:
    tenant_id = _create_committed_tenant()
    idempotency_key = f"billing-operation-{uuid4()}"
    barrier = Barrier(2)

    def create_operation() -> str:
        with Session(get_engine()) as session:
            repository = ProviderOperationRepository()
            barrier.wait(timeout=5)

            try:
                repository.add_and_flush(
                    session,
                    ProviderOperation(
                        tenant_id=tenant_id,
                        provider=BillingProvider.FAKE,
                        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
                        idempotency_key=idempotency_key,
                        request_fingerprint="a" * 64,
                        request_payload={
                            "price_code": "starter_monthly",
                        },
                    ),
                )
                session.commit()
            except ProviderOperationAlreadyExistsError:
                session.rollback()
                return "conflict"

            return "created"

    try:
        outcomes = _run_two_workers(create_operation)

        assert sorted(outcomes) == [
            "conflict",
            "created",
        ]

        with Session(get_engine()) as session:
            operation_ids = session.scalars(
                select(ProviderOperation.id).where(
                    ProviderOperation.tenant_id == tenant_id,
                    ProviderOperation.operation_type == (ProviderOperationType.CREATE_SUBSCRIPTION),
                    ProviderOperation.idempotency_key == idempotency_key,
                )
            ).all()

        assert len(operation_ids) == 1
    finally:
        _cleanup_tenant(tenant_id=tenant_id)


def test_concurrent_webhook_delivery_preserves_event_uniqueness() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    barrier = Barrier(2)

    def create_event() -> str:
        with Session(get_engine()) as session:
            repository = BillingWebhookEventRepository()
            barrier.wait(timeout=5)

            try:
                repository.add_and_flush(
                    session,
                    BillingWebhookEvent(
                        provider=BillingProvider.FAKE,
                        provider_event_id=provider_event_id,
                        event_type="subscription.updated",
                        provider_state_version=1,
                        payload={
                            "provider_subscription_id": (f"sub_{uuid4().hex}"),
                        },
                    ),
                )
                session.commit()
            except BillingWebhookEventAlreadyExistsError:
                session.rollback()
                return "conflict"

            return "created"

    try:
        outcomes = _run_two_workers(create_event)

        assert sorted(outcomes) == [
            "conflict",
            "created",
        ]

        with Session(get_engine()) as session:
            event_ids = session.scalars(
                select(BillingWebhookEvent.id).where(
                    BillingWebhookEvent.provider == BillingProvider.FAKE,
                    BillingWebhookEvent.provider_event_id == provider_event_id,
                )
            ).all()

        assert len(event_ids) == 1
    finally:
        _cleanup_webhook_event(provider_event_id=provider_event_id)


def test_subscription_for_update_blocks_a_competing_lock() -> None:
    tenant_id = _create_committed_tenant()
    customer_id = _create_committed_customer(tenant_id=tenant_id)
    subscription_id = _create_committed_subscription(
        tenant_id=tenant_id,
        billing_customer_id=customer_id,
    )
    repository = SubscriptionRepository()
    locking_session = Session(get_engine())

    try:
        locked = repository.get_by_tenant_id_for_update(
            locking_session,
            tenant_id=tenant_id,
        )

        assert locked is not None
        assert locked.id == subscription_id

        with Session(get_engine()) as competing_session:
            competing_session.execute(text("SET LOCAL lock_timeout = '250ms'"))

            with pytest.raises(DBAPIError) as exception_info:
                repository.get_by_tenant_id_for_update(
                    competing_session,
                    tenant_id=tenant_id,
                )

            assert isinstance(
                exception_info.value.orig,
                LockNotAvailable,
            )
            competing_session.rollback()

        locking_session.commit()

        with Session(get_engine()) as verification_session:
            reacquired = repository.get_by_tenant_id_for_update(
                verification_session,
                tenant_id=tenant_id,
            )

            assert reacquired is not None
            assert reacquired.id == subscription_id

            verification_session.rollback()
    finally:
        locking_session.rollback()
        locking_session.close()
        _cleanup_tenant(tenant_id=tenant_id)
