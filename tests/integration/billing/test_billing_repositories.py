from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingCustomerAlreadyExistsError,
    BillingError,
    BillingProviderCustomerAlreadyLinkedError,
    BillingProviderSubscriptionAlreadyLinkedError,
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


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _create_tenant(session: Session) -> Tenant:
    tenant = Tenant(name=f"Billing Repository {uuid4().hex}")
    session.add(tenant)
    session.flush()
    return tenant


def _new_customer(
    *,
    tenant_id: UUID,
    provider_customer_id: str | None = None,
) -> BillingCustomer:
    return BillingCustomer(
        tenant_id=tenant_id,
        provider=BillingProvider.FAKE,
        provider_customer_id=provider_customer_id,
    )


def _new_subscription(
    *,
    tenant_id: UUID,
    billing_customer_id: UUID,
    provider_subscription_id: str | None = None,
) -> Subscription:
    return Subscription(
        tenant_id=tenant_id,
        billing_customer_id=billing_customer_id,
        provider=BillingProvider.FAKE,
        provider_subscription_id=provider_subscription_id,
        price_code="starter_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=4_900,
        status=SubscriptionStatus.PENDING,
    )


def _new_operation(
    *,
    tenant_id: UUID,
    idempotency_key: str,
) -> ProviderOperation:
    return ProviderOperation(
        tenant_id=tenant_id,
        provider=BillingProvider.FAKE,
        operation_type=ProviderOperationType.CREATE_SUBSCRIPTION,
        idempotency_key=idempotency_key,
        request_fingerprint="a" * 64,
        request_payload={
            "price_code": "starter_monthly",
        },
    )


def _new_event(
    *,
    provider_event_id: str,
) -> BillingWebhookEvent:
    provider_created_at = datetime(
        2026,
        8,
        22,
        12,
        tzinfo=UTC,
    )
    provider_subscription_id = f"fake_sub_{uuid4().hex}"

    return BillingWebhookEvent(
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
        provider_subscription_id=(provider_subscription_id),
        provider_created_at=provider_created_at,
        provider_state_version=1,
        payload={
            "id": provider_event_id,
            "type": (BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
            "created_at": (provider_created_at.isoformat()),
            "data": {
                "provider_subscription_id": (provider_subscription_id),
                "provider_state_version": 1,
                "price_code": "starter_monthly",
                "status": "active",
                "current_period_start": ("2026-08-22T12:00:00+00:00"),
                "current_period_end": ("2026-09-22T12:00:00+00:00"),
                "canceled_at": None,
            },
        },
        payload_sha256="a" * 64,
        signature_timestamp=int(provider_created_at.timestamp()),
    )


def test_billing_customer_repository_adds_and_resolves_customer(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    repository = BillingCustomerRepository()

    customer = repository.add_and_flush(
        db_session,
        _new_customer(tenant_id=tenant.id),
    )

    by_tenant = repository.get_by_tenant_and_provider(
        db_session,
        tenant_id=tenant.id,
        provider=BillingProvider.FAKE,
    )
    locked_by_tenant = repository.get_by_tenant_and_provider_for_update(
        db_session,
        tenant_id=tenant.id,
        provider=BillingProvider.FAKE,
    )
    locked_by_id = repository.get_by_id_for_update(
        db_session,
        billing_customer_id=customer.id,
    )

    assert by_tenant is customer
    assert locked_by_tenant is customer
    assert locked_by_id is customer
    assert db_session.in_transaction() is True


def test_billing_customer_repository_flushes_updates(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    repository = BillingCustomerRepository()
    customer = repository.add_and_flush(
        db_session,
        _new_customer(tenant_id=tenant.id),
    )

    provider_customer_id = f"cus_{uuid4().hex}"
    customer.provider_customer_id = provider_customer_id
    repository.flush(db_session)

    refreshed = repository.get_by_tenant_and_provider(
        db_session,
        tenant_id=tenant.id,
        provider=BillingProvider.FAKE,
    )

    assert refreshed is not None
    assert refreshed.provider_customer_id == provider_customer_id


def test_billing_customer_repository_translates_tenant_provider_conflict(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    repository = BillingCustomerRepository()

    repository.add_and_flush(
        db_session,
        _new_customer(tenant_id=tenant.id),
    )

    with pytest.raises(BillingCustomerAlreadyExistsError):
        repository.add_and_flush(
            db_session,
            _new_customer(tenant_id=tenant.id),
        )


def test_billing_customer_repository_translates_provider_id_conflict(
    db_session: Session,
) -> None:
    first_tenant = _create_tenant(db_session)
    second_tenant = _create_tenant(db_session)
    repository = BillingCustomerRepository()
    provider_customer_id = f"cus_{uuid4().hex}"

    repository.add_and_flush(
        db_session,
        _new_customer(
            tenant_id=first_tenant.id,
            provider_customer_id=provider_customer_id,
        ),
    )

    with pytest.raises(BillingProviderCustomerAlreadyLinkedError):
        repository.add_and_flush(
            db_session,
            _new_customer(
                tenant_id=second_tenant.id,
                provider_customer_id=provider_customer_id,
            ),
        )


def test_subscription_repository_adds_and_resolves_subscription(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    customer = BillingCustomerRepository().add_and_flush(
        db_session,
        _new_customer(tenant_id=tenant.id),
    )
    repository = SubscriptionRepository()

    subscription = repository.add_and_flush(
        db_session,
        _new_subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
        ),
    )

    by_tenant = repository.get_by_tenant_id(
        db_session,
        tenant_id=tenant.id,
    )
    locked_by_tenant = repository.get_by_tenant_id_for_update(
        db_session,
        tenant_id=tenant.id,
    )
    locked_by_id = repository.get_by_id_for_update(
        db_session,
        subscription_id=subscription.id,
    )

    assert by_tenant is subscription
    assert locked_by_tenant is subscription
    assert locked_by_id is subscription
    assert db_session.in_transaction() is True


def test_subscription_repository_flushes_updates(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    customer = BillingCustomerRepository().add_and_flush(
        db_session,
        _new_customer(tenant_id=tenant.id),
    )
    repository = SubscriptionRepository()
    subscription = repository.add_and_flush(
        db_session,
        _new_subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
        ),
    )

    period_start = datetime(2026, 7, 1, tzinfo=UTC)
    period_end = datetime(2026, 8, 1, tzinfo=UTC)

    subscription.status = SubscriptionStatus.ACTIVE
    subscription.current_period_start = period_start
    subscription.current_period_end = period_end
    repository.flush(db_session)

    refreshed = repository.get_by_tenant_id(
        db_session,
        tenant_id=tenant.id,
    )

    assert refreshed is not None
    assert refreshed.status is SubscriptionStatus.ACTIVE
    assert refreshed.current_period_start == period_start
    assert refreshed.current_period_end == period_end


def test_subscription_repository_translates_tenant_conflict(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    customer = BillingCustomerRepository().add_and_flush(
        db_session,
        _new_customer(tenant_id=tenant.id),
    )
    repository = SubscriptionRepository()

    repository.add_and_flush(
        db_session,
        _new_subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
        ),
    )

    with pytest.raises(BillingSubscriptionAlreadyExistsError):
        repository.add_and_flush(
            db_session,
            _new_subscription(
                tenant_id=tenant.id,
                billing_customer_id=customer.id,
            ),
        )


def test_subscription_repository_translates_provider_id_conflict(
    db_session: Session,
) -> None:
    first_tenant = _create_tenant(db_session)
    second_tenant = _create_tenant(db_session)
    customer_repository = BillingCustomerRepository()
    first_customer = customer_repository.add_and_flush(
        db_session,
        _new_customer(tenant_id=first_tenant.id),
    )
    second_customer = customer_repository.add_and_flush(
        db_session,
        _new_customer(tenant_id=second_tenant.id),
    )
    repository = SubscriptionRepository()
    provider_subscription_id = f"sub_{uuid4().hex}"

    repository.add_and_flush(
        db_session,
        _new_subscription(
            tenant_id=first_tenant.id,
            billing_customer_id=first_customer.id,
            provider_subscription_id=provider_subscription_id,
        ),
    )

    with pytest.raises(BillingProviderSubscriptionAlreadyLinkedError):
        repository.add_and_flush(
            db_session,
            _new_subscription(
                tenant_id=second_tenant.id,
                billing_customer_id=second_customer.id,
                provider_subscription_id=provider_subscription_id,
            ),
        )


def test_provider_operation_repository_adds_and_resolves_operation(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    repository = ProviderOperationRepository()
    idempotency_key = f"operation-{uuid4()}"

    operation = repository.add_and_flush(
        db_session,
        _new_operation(
            tenant_id=tenant.id,
            idempotency_key=idempotency_key,
        ),
    )

    by_key = repository.get_by_idempotency_key(
        db_session,
        tenant_id=tenant.id,
        operation_type=ProviderOperationType.CREATE_SUBSCRIPTION,
        idempotency_key=idempotency_key,
    )
    locked_by_key = repository.get_by_idempotency_key_for_update(
        db_session,
        tenant_id=tenant.id,
        operation_type=ProviderOperationType.CREATE_SUBSCRIPTION,
        idempotency_key=idempotency_key,
    )
    locked_by_id = repository.get_by_id_for_update(
        db_session,
        provider_operation_id=operation.id,
    )

    assert by_key is operation
    assert locked_by_key is operation
    assert locked_by_id is operation
    assert db_session.in_transaction() is True


def test_provider_operation_repository_flushes_lifecycle_updates(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    repository = ProviderOperationRepository()
    operation = repository.add_and_flush(
        db_session,
        _new_operation(
            tenant_id=tenant.id,
            idempotency_key=f"operation-{uuid4()}",
        ),
    )
    started_at = datetime(2026, 7, 21, tzinfo=UTC)

    operation.status = ProviderOperationStatus.IN_PROGRESS
    operation.started_at = started_at
    operation.attempt_count = 1
    repository.flush(db_session)

    locked = repository.get_by_id_for_update(
        db_session,
        provider_operation_id=operation.id,
    )

    assert locked is not None
    assert locked.status is ProviderOperationStatus.IN_PROGRESS
    assert locked.started_at == started_at
    assert locked.attempt_count == 1


def test_provider_operation_repository_translates_idempotency_conflict(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session)
    repository = ProviderOperationRepository()
    idempotency_key = f"operation-{uuid4()}"

    repository.add_and_flush(
        db_session,
        _new_operation(
            tenant_id=tenant.id,
            idempotency_key=idempotency_key,
        ),
    )

    with pytest.raises(ProviderOperationAlreadyExistsError):
        repository.add_and_flush(
            db_session,
            _new_operation(
                tenant_id=tenant.id,
                idempotency_key=idempotency_key,
            ),
        )


def test_webhook_event_repository_adds_and_resolves_event(
    db_session: Session,
) -> None:
    repository = BillingWebhookEventRepository()
    provider_event_id = f"evt_{uuid4().hex}"

    event = repository.add_and_flush(
        db_session,
        _new_event(provider_event_id=provider_event_id),
    )

    by_provider_id = repository.get_by_provider_event_id(
        db_session,
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
    )
    locked_by_provider_id = repository.get_by_provider_event_id_for_update(
        db_session,
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
    )
    locked_by_id = repository.get_by_id_for_update(
        db_session,
        webhook_event_id=event.id,
    )

    assert by_provider_id is event
    assert locked_by_provider_id is event
    assert locked_by_id is event
    assert db_session.in_transaction() is True


def test_webhook_event_repository_flushes_processing_updates(
    db_session: Session,
) -> None:
    repository = BillingWebhookEventRepository()
    event = repository.add_and_flush(
        db_session,
        _new_event(provider_event_id=f"evt_{uuid4().hex}"),
    )
    processed_at = datetime(2026, 7, 21, tzinfo=UTC)

    event.status = BillingWebhookEventStatus.PROCESSED
    event.processing_attempt_count = 1
    event.processed_at = processed_at
    repository.flush(db_session)

    locked = repository.get_by_id_for_update(
        db_session,
        webhook_event_id=event.id,
    )

    assert locked is not None
    assert locked.status is BillingWebhookEventStatus.PROCESSED
    assert locked.processing_attempt_count == 1
    assert locked.processed_at == processed_at


def test_webhook_event_repository_translates_duplicate_event(
    db_session: Session,
) -> None:
    repository = BillingWebhookEventRepository()
    provider_event_id = f"evt_{uuid4().hex}"

    repository.add_and_flush(
        db_session,
        _new_event(provider_event_id=provider_event_id),
    )

    with pytest.raises(BillingWebhookEventAlreadyExistsError):
        repository.add_and_flush(
            db_session,
            _new_event(provider_event_id=provider_event_id),
        )


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            BillingCustomerAlreadyExistsError(),
            "billing_customer_already_exists",
        ),
        (
            BillingProviderCustomerAlreadyLinkedError(),
            "billing_provider_customer_already_linked",
        ),
        (
            BillingSubscriptionAlreadyExistsError(),
            "billing_subscription_already_exists",
        ),
        (
            BillingProviderSubscriptionAlreadyLinkedError(),
            "billing_provider_subscription_already_linked",
        ),
        (
            ProviderOperationAlreadyExistsError(),
            "provider_operation_already_exists",
        ),
        (
            BillingWebhookEventAlreadyExistsError(),
            "billing_webhook_event_already_exists",
        ),
    ],
)
def test_billing_repository_conflicts_have_stable_codes(
    error: BillingError,
    expected_code: str,
) -> None:
    assert error.code == expected_code
