from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg.errors import CheckViolation, UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
    WebhookEventStatus,
)
from clinicops.billing.models import (
    BillingCustomer,
    BillingWebhookEvent,
    ProviderOperation,
    Subscription,
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


def _tenant(session: Session) -> Tenant:
    tenant = Tenant(name=f"Billing Schema {uuid4().hex}")
    session.add(tenant)
    session.flush()
    return tenant


def _billing_customer(
    session: Session,
    tenant: Tenant,
    *,
    provider_customer_id: str | None = None,
) -> BillingCustomer:
    customer = BillingCustomer(
        tenant_id=tenant.id,
        provider=BillingProvider.FAKE,
        provider_customer_id=provider_customer_id,
    )
    session.add(customer)
    session.flush()
    return customer


def _subscription(
    session: Session,
    tenant: Tenant,
    customer: BillingCustomer,
    *,
    provider_subscription_id: str | None = None,
    price_code: str = "starter_monthly",
    pending_price_code: str | None = None,
    status: SubscriptionStatus = SubscriptionStatus.PENDING,
    unit_amount: int = 4_900,
    provider_state_version: int = 0,
    current_period_start: datetime | None = None,
    current_period_end: datetime | None = None,
    cancel_at_period_end: bool = False,
    cancellation_requested_at: datetime | None = None,
    canceled_at: datetime | None = None,
) -> Subscription:
    subscription = Subscription(
        tenant_id=tenant.id,
        billing_customer_id=customer.id,
        provider=BillingProvider.FAKE,
        provider_subscription_id=provider_subscription_id,
        price_code=price_code,
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=unit_amount,
        pending_price_code=pending_price_code,
        status=status,
        cancel_at_period_end=cancel_at_period_end,
        cancellation_requested_at=cancellation_requested_at,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        provider_state_version=provider_state_version,
        canceled_at=canceled_at,
    )
    session.add(subscription)
    session.flush()
    return subscription


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def test_billing_schema_persists_all_foundation_entities(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    customer = _billing_customer(db_session, tenant)
    subscription = _subscription(
        db_session,
        tenant,
        customer,
    )

    operation = ProviderOperation(
        tenant_id=tenant.id,
        billing_customer_id=customer.id,
        subscription_id=subscription.id,
        provider=BillingProvider.FAKE,
        operation_type=ProviderOperationType.CREATE_SUBSCRIPTION,
        idempotency_key=f"create-{uuid4()}",
        request_fingerprint="a" * 64,
        request_payload={
            "price_code": "starter_monthly",
        },
    )
    event = BillingWebhookEvent(
        provider=BillingProvider.FAKE,
        provider_event_id=f"evt_{uuid4().hex}",
        event_type="subscription.created",
        provider_state_version=1,
        payload={
            "subscription_id": "fake_subscription",
        },
        correlation_id=str(uuid4()),
    )

    db_session.add_all([operation, event])
    db_session.flush()

    assert customer.provider is BillingProvider.FAKE
    assert customer.provider_customer_id is None

    assert subscription.status is SubscriptionStatus.PENDING
    assert subscription.provider_subscription_id is None
    assert subscription.provider_state_version == 0
    assert subscription.cancel_at_period_end is False

    assert operation.status is ProviderOperationStatus.PENDING
    assert operation.attempt_count == 0
    assert operation.request_payload == {
        "price_code": "starter_monthly",
    }

    assert event.status is WebhookEventStatus.RECEIVED
    assert event.payload == {
        "subscription_id": "fake_subscription",
    }


def test_billing_customer_is_unique_per_tenant_and_provider(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    _billing_customer(db_session, tenant)

    db_session.add(
        BillingCustomer(
            tenant_id=tenant.id,
            provider=BillingProvider.FAKE,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, UniqueViolation)
    assert _constraint_name(exception_info.value) == "uq_billing_customers_tenant_id_provider"


def test_provider_customer_identifier_is_unique_when_present(
    db_session: Session,
) -> None:
    first_tenant = _tenant(db_session)
    second_tenant = _tenant(db_session)
    provider_customer_id = f"cus_{uuid4().hex}"

    _billing_customer(
        db_session,
        first_tenant,
        provider_customer_id=provider_customer_id,
    )

    db_session.add(
        BillingCustomer(
            tenant_id=second_tenant.id,
            provider=BillingProvider.FAKE,
            provider_customer_id=provider_customer_id,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, UniqueViolation)
    assert _constraint_name(exception_info.value) == "uq_billing_customers_provider_customer_id"


def test_multiple_pending_provider_customer_identifiers_are_allowed(
    db_session: Session,
) -> None:
    first_tenant = _tenant(db_session)
    second_tenant = _tenant(db_session)

    first_customer = _billing_customer(
        db_session,
        first_tenant,
    )
    second_customer = _billing_customer(
        db_session,
        second_tenant,
    )

    assert first_customer.provider_customer_id is None
    assert second_customer.provider_customer_id is None


def test_subscription_is_unique_per_tenant(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    customer = _billing_customer(db_session, tenant)
    _subscription(db_session, tenant, customer)

    db_session.add(
        Subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
            provider=BillingProvider.FAKE,
            price_code="professional_monthly",
            plan=BillingPlan.PROFESSIONAL,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=9_900,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, UniqueViolation)
    assert _constraint_name(exception_info.value) == "uq_subscriptions_tenant_id"


def test_provider_subscription_identifier_is_unique_when_present(
    db_session: Session,
) -> None:
    first_tenant = _tenant(db_session)
    second_tenant = _tenant(db_session)
    first_customer = _billing_customer(
        db_session,
        first_tenant,
    )
    second_customer = _billing_customer(
        db_session,
        second_tenant,
    )
    provider_subscription_id = f"sub_{uuid4().hex}"

    _subscription(
        db_session,
        first_tenant,
        first_customer,
        provider_subscription_id=provider_subscription_id,
    )

    db_session.add(
        Subscription(
            tenant_id=second_tenant.id,
            billing_customer_id=second_customer.id,
            provider=BillingProvider.FAKE,
            provider_subscription_id=provider_subscription_id,
            price_code="starter_monthly",
            plan=BillingPlan.STARTER,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=4_900,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, UniqueViolation)
    assert _constraint_name(exception_info.value) == "uq_subscriptions_provider_subscription_id"


def test_provider_operation_idempotency_scope_is_unique(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    idempotency_key = f"idempotency-{uuid4()}"

    first = ProviderOperation(
        tenant_id=tenant.id,
        provider=BillingProvider.FAKE,
        operation_type=ProviderOperationType.CREATE_SUBSCRIPTION,
        idempotency_key=idempotency_key,
        request_fingerprint="b" * 64,
        request_payload={
            "price_code": "starter_monthly",
        },
    )
    duplicate = ProviderOperation(
        tenant_id=tenant.id,
        provider=BillingProvider.FAKE,
        operation_type=ProviderOperationType.CREATE_SUBSCRIPTION,
        idempotency_key=idempotency_key,
        request_fingerprint="b" * 64,
        request_payload={
            "price_code": "starter_monthly",
        },
    )

    db_session.add(first)
    db_session.flush()
    db_session.add(duplicate)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, UniqueViolation)
    assert _constraint_name(exception_info.value) == "uq_provider_operations_tenant_op_idempotency"


def test_same_client_key_is_allowed_for_another_operation_type(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    idempotency_key = f"idempotency-{uuid4()}"

    db_session.add_all(
        [
            ProviderOperation(
                tenant_id=tenant.id,
                provider=BillingProvider.FAKE,
                operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
                idempotency_key=idempotency_key,
                request_fingerprint="c" * 64,
                request_payload={
                    "price_code": "starter_monthly",
                },
            ),
            ProviderOperation(
                tenant_id=tenant.id,
                provider=BillingProvider.FAKE,
                operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
                idempotency_key=idempotency_key,
                request_fingerprint="d" * 64,
                request_payload={},
            ),
        ]
    )

    db_session.flush()


def test_webhook_event_is_unique_per_provider(
    db_session: Session,
) -> None:
    provider_event_id = f"evt_{uuid4().hex}"

    db_session.add(
        BillingWebhookEvent(
            provider=BillingProvider.FAKE,
            provider_event_id=provider_event_id,
            event_type="subscription.created",
            payload={},
        )
    )
    db_session.flush()

    db_session.add(
        BillingWebhookEvent(
            provider=BillingProvider.FAKE,
            provider_event_id=provider_event_id,
            event_type="subscription.created",
            payload={},
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, UniqueViolation)
    assert _constraint_name(exception_info.value) == "uq_billing_webhook_events_provider_event"


@pytest.mark.parametrize(
    (
        "unit_amount",
        "provider_state_version",
        "pending_price_code",
        "expected_constraint",
    ),
    [
        (
            0,
            0,
            None,
            "ck_subscriptions_unit_amount_positive",
        ),
        (
            4_900,
            -1,
            None,
            ("ck_subscriptions_provider_state_version_nonnegative"),
        ),
        (
            4_900,
            0,
            "starter_monthly",
            "ck_subscriptions_pending_price_differs",
        ),
    ],
)
def test_subscription_rejects_invalid_numeric_or_price_state(
    db_session: Session,
    unit_amount: int,
    provider_state_version: int,
    pending_price_code: str | None,
    expected_constraint: str,
) -> None:
    tenant = _tenant(db_session)
    customer = _billing_customer(db_session, tenant)

    db_session.add(
        Subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
            provider=BillingProvider.FAKE,
            price_code="starter_monthly",
            plan=BillingPlan.STARTER,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=unit_amount,
            pending_price_code=pending_price_code,
            provider_state_version=provider_state_version,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == expected_constraint


def test_active_subscription_requires_a_complete_period(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    customer = _billing_customer(db_session, tenant)

    db_session.add(
        Subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
            provider=BillingProvider.FAKE,
            price_code="starter_monthly",
            plan=BillingPlan.STARTER,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=4_900,
            status=SubscriptionStatus.ACTIVE,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == "ck_subscriptions_active_period"


def test_subscription_rejects_incomplete_period_pair(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    customer = _billing_customer(db_session, tenant)

    db_session.add(
        Subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
            provider=BillingProvider.FAKE,
            price_code="starter_monthly",
            plan=BillingPlan.STARTER,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=4_900,
            current_period_start=datetime(
                2026,
                7,
                1,
                tzinfo=UTC,
            ),
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == "ck_subscriptions_period_pair"


def test_canceled_subscription_requires_consistent_state(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    customer = _billing_customer(db_session, tenant)

    db_session.add(
        Subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
            provider=BillingProvider.FAKE,
            price_code="starter_monthly",
            plan=BillingPlan.STARTER,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=4_900,
            status=SubscriptionStatus.CANCELED,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == "ck_subscriptions_cancellation_consistency"


def test_scheduled_cancellation_requires_request_timestamp(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    customer = _billing_customer(db_session, tenant)

    db_session.add(
        Subscription(
            tenant_id=tenant.id,
            billing_customer_id=customer.id,
            provider=BillingProvider.FAKE,
            price_code="starter_monthly",
            plan=BillingPlan.STARTER,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=4_900,
            cancel_at_period_end=True,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == "ck_subscriptions_cancellation_consistency"


@pytest.mark.parametrize(
    (
        "fingerprint",
        "attempt_count",
        "expected_constraint",
    ),
    [
        (
            "not-a-sha256",
            0,
            "ck_provider_operations_fingerprint_format",
        ),
        (
            "e" * 64,
            -1,
            ("ck_provider_operations_attempt_count_nonnegative"),
        ),
    ],
)
def test_provider_operation_rejects_invalid_core_state(
    db_session: Session,
    fingerprint: str,
    attempt_count: int,
    expected_constraint: str,
) -> None:
    tenant = _tenant(db_session)

    db_session.add(
        ProviderOperation(
            tenant_id=tenant.id,
            provider=BillingProvider.FAKE,
            operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
            idempotency_key=f"key-{uuid4()}",
            request_fingerprint=fingerprint,
            request_payload={},
            attempt_count=attempt_count,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == expected_constraint


def test_failed_provider_operation_requires_failure_code(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)

    db_session.add(
        ProviderOperation(
            tenant_id=tenant.id,
            provider=BillingProvider.FAKE,
            operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
            idempotency_key=f"key-{uuid4()}",
            request_fingerprint="f" * 64,
            request_payload={},
            status=ProviderOperationStatus.FAILED_RETRYABLE,
            started_at=datetime(2026, 7, 21, tzinfo=UTC),
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == "ck_provider_operations_failure_consistency"


def test_processed_webhook_event_requires_processed_timestamp(
    db_session: Session,
) -> None:
    db_session.add(
        BillingWebhookEvent(
            provider=BillingProvider.FAKE,
            provider_event_id=f"evt_{uuid4().hex}",
            event_type="subscription.updated",
            payload={},
            status=WebhookEventStatus.PROCESSED,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == "ck_billing_webhook_events_status_consistency"


def test_webhook_event_rejects_negative_provider_version(
    db_session: Session,
) -> None:
    db_session.add(
        BillingWebhookEvent(
            provider=BillingProvider.FAKE,
            provider_event_id=f"evt_{uuid4().hex}",
            event_type="subscription.updated",
            provider_state_version=-1,
            payload={},
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert isinstance(exception_info.value.orig, CheckViolation)
    assert _constraint_name(exception_info.value) == "ck_billing_webhook_events_version_nonnegative"
