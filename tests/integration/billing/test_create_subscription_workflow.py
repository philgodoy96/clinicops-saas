from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    ProviderOperationStatus,
    ProviderOperationType,
)
from clinicops.billing.exceptions import (
    BillingIdempotencyConflictError,
    BillingSubscriptionAlreadyExistsError,
)
from clinicops.billing.models import (
    BillingCustomer,
    ProviderOperation,
    Subscription,
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
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionCommand,
    CreateBillingSubscriptionService,
    CreatedBillingSubscription,
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


@pytest.fixture
def tenant_id() -> Iterator[UUID]:
    tracked_tenant_id = uuid4()

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tracked_tenant_id,
                name=(f"Billing Workflow Clinic {tracked_tenant_id.hex}"),
            )
        )
        session.commit()

    try:
        yield tracked_tenant_id
    finally:
        _cleanup_tenant(tracked_tenant_id)


def _build_service(
    provider: FakePaymentProvider,
) -> CreateBillingSubscriptionService:
    return CreateBillingSubscriptionService(
        payment_provider=provider,
        clock=FixedClock(),
    )


def _command(
    tenant_id: UUID,
    *,
    price_code: str = "starter_monthly",
    idempotency_key: str = "billing-request-123",
) -> CreateBillingSubscriptionCommand:
    return CreateBillingSubscriptionCommand(
        tenant_id=tenant_id,
        price_code=price_code,
        idempotency_key=idempotency_key,
    )


def _execute(
    service: CreateBillingSubscriptionService,
    command: CreateBillingSubscriptionCommand,
) -> CreatedBillingSubscription:
    with Session(get_engine()) as session:
        return service.execute(
            session,
            command,
        )


def _load_operations(
    tenant_id: UUID,
) -> dict[ProviderOperationType, ProviderOperation]:
    with Session(get_engine()) as session:
        operations = session.scalars(
            select(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id)
        ).all()

        return {operation.operation_type: operation for operation in operations}


def _cleanup_tenant(tenant_id: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id))
        session.execute(delete(Subscription).where(Subscription.tenant_id == tenant_id))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def test_creation_persists_customer_subscription_and_operations(
    tenant_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)

    result = _execute(
        service,
        _command(tenant_id),
    )

    assert result.tenant_id == tenant_id
    assert result.price_code == "starter_monthly"
    assert result.replayed is False

    with Session(get_engine()) as session:
        customer = session.scalar(
            select(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id)
        )
        subscription = session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )

        assert customer is not None
        assert customer.provider_customer_id is not None
        assert customer.provider_customer_id.startswith("fake_cus_")
        assert subscription is not None
        assert subscription.id == result.id
        assert subscription.provider_subscription_id is not None
        assert subscription.provider_subscription_id.startswith("fake_sub_")
        assert subscription.provider_state_version == 1

    operations = _load_operations(tenant_id)

    assert set(operations) == {
        ProviderOperationType.CREATE_CUSTOMER,
        ProviderOperationType.CREATE_SUBSCRIPTION,
    }
    assert all(
        operation.status is ProviderOperationStatus.SUCCEEDED for operation in operations.values()
    )
    assert all(operation.attempt_count == 1 for operation in operations.values())
    customer_payload = operations[ProviderOperationType.CREATE_CUSTOMER].result_payload
    subscription_payload = operations[ProviderOperationType.CREATE_SUBSCRIPTION].result_payload

    assert customer_payload is not None
    assert subscription_payload is not None
    assert customer_payload["provider_customer_id"] == customer.provider_customer_id
    assert subscription_payload["provider_subscription_id"] == subscription.provider_subscription_id


def test_successful_request_replays_from_persisted_subscription(
    tenant_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)
    command = _command(tenant_id)

    first = _execute(service, command)
    replayed = _execute(service, command)

    assert replayed.id == first.id
    assert replayed.replayed is True

    with Session(get_engine()) as session:
        operations = session.scalars(
            select(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id)
        ).all()
        subscriptions = session.scalars(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        ).all()

        assert len(operations) == 2
        assert len(subscriptions) == 1


def test_same_key_with_different_price_is_rejected(
    tenant_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)
    idempotency_key = "billing-request-conflict"

    _execute(
        service,
        _command(
            tenant_id,
            idempotency_key=idempotency_key,
        ),
    )

    with pytest.raises(BillingIdempotencyConflictError):
        _execute(
            service,
            _command(
                tenant_id,
                price_code="professional_monthly",
                idempotency_key=idempotency_key,
            ),
        )


def test_existing_subscription_with_new_key_is_rejected(
    tenant_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)

    _execute(
        service,
        _command(
            tenant_id,
            idempotency_key="first-request",
        ),
    )

    with pytest.raises(BillingSubscriptionAlreadyExistsError):
        _execute(
            service,
            _command(
                tenant_id,
                idempotency_key="second-request",
            ),
        )


def test_ambiguous_customer_result_resumes_without_duplication(
    tenant_id: UUID,
) -> None:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)
    service = _build_service(provider)
    command = _command(
        tenant_id,
        idempotency_key="ambiguous-customer-request",
    )
    control.queue_outcome(
        operation_type=ProviderOperationType.CREATE_CUSTOMER,
        outcome=FakeProviderOutcome.AMBIGUOUS_SUCCESS,
    )

    with pytest.raises(ProviderAmbiguousOutcomeError):
        _execute(service, command)

    recovered = _execute(service, command)
    operations = _load_operations(tenant_id)

    assert recovered.replayed is False
    assert operations[ProviderOperationType.CREATE_CUSTOMER].attempt_count == 2
    assert (
        operations[ProviderOperationType.CREATE_CUSTOMER].status
        is ProviderOperationStatus.SUCCEEDED
    )
    assert operations[ProviderOperationType.CREATE_SUBSCRIPTION].attempt_count == 1
    assert (
        operations[ProviderOperationType.CREATE_SUBSCRIPTION].status
        is ProviderOperationStatus.SUCCEEDED
    )

    with Session(get_engine()) as session:
        customers = session.scalars(
            select(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id)
        ).all()
        subscriptions = session.scalars(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        ).all()

        assert len(customers) == 1
        assert len(subscriptions) == 1


def test_terminal_subscription_failure_is_persisted_and_replayed(
    tenant_id: UUID,
) -> None:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)
    service = _build_service(provider)
    command = _command(
        tenant_id,
        idempotency_key="terminal-subscription-request",
    )
    control.queue_outcome(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        outcome=FakeProviderOutcome.TERMINAL_REJECTION,
    )

    with pytest.raises(ProviderTerminalError):
        _execute(service, command)

    operations = _load_operations(tenant_id)
    subscription_operation = operations[ProviderOperationType.CREATE_SUBSCRIPTION]

    assert subscription_operation.status is ProviderOperationStatus.FAILED_TERMINAL
    assert subscription_operation.attempt_count == 1
    assert subscription_operation.failure_code == ("provider_terminal_failure")

    with pytest.raises(ProviderTerminalError):
        _execute(service, command)

    replayed_operations = _load_operations(tenant_id)

    assert replayed_operations[ProviderOperationType.CREATE_SUBSCRIPTION].attempt_count == 1

    with Session(get_engine()) as session:
        subscription = session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )

        assert subscription is None
