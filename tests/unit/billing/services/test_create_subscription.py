from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingProvider,
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
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
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.providers.contracts import (
    CreateCustomerRequest,
    CreateCustomerResult,
    CreateSubscriptionRequest,
    CreateSubscriptionResult,
)
from clinicops.billing.repositories.billing_customer_repository import (
    BillingCustomerRepository,
)
from clinicops.billing.repositories.provider_operation_repository import (
    ProviderOperationRepository,
)
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
)
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionCommand,
    CreateBillingSubscriptionService,
)
from clinicops.core.clock import Clock

FIXED_NOW = datetime(
    2026,
    7,
    22,
    12,
    tzinfo=UTC,
)
PERIOD_END = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
TENANT_ID = UUID("5c9d9f0b-bffd-4519-85e4-817f365daee8")


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0
        self._in_transaction = True

    def commit(self) -> None:
        self.commit_count += 1
        self._in_transaction = False

    def rollback(self) -> None:
        self.rollback_count += 1
        self._in_transaction = False

    def flush(self) -> None:
        self.flush_count += 1
        self._in_transaction = True

    def in_transaction(self) -> bool:
        return self._in_transaction

    def touch(self) -> None:
        self._in_transaction = True


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


class InMemoryBillingCustomerRepository:
    def __init__(
        self,
        customer: BillingCustomer | None = None,
    ) -> None:
        self.customer = customer

    def get_by_tenant_and_provider_for_update(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        provider: BillingProvider,
    ) -> BillingCustomer | None:
        cast(FakeSession, session).touch()

        if (
            self.customer is not None
            and self.customer.tenant_id == tenant_id
            and self.customer.provider is provider
        ):
            return self.customer

        return None

    def add_and_flush(
        self,
        session: Session,
        customer: BillingCustomer,
    ) -> None:
        cast(FakeSession, session).touch()
        customer.created_at = FIXED_NOW
        customer.updated_at = FIXED_NOW
        self.customer = customer


class InMemorySubscriptionRepository:
    def __init__(
        self,
        subscription: Subscription | None = None,
    ) -> None:
        self.subscription = subscription

    def get_by_tenant_id_for_update(
        self,
        session: Session,
        *,
        tenant_id: UUID,
    ) -> Subscription | None:
        cast(FakeSession, session).touch()

        if self.subscription is not None and self.subscription.tenant_id == tenant_id:
            return self.subscription

        return None

    def add_and_flush(
        self,
        session: Session,
        subscription: Subscription,
    ) -> None:
        cast(FakeSession, session).touch()
        subscription.created_at = FIXED_NOW
        subscription.updated_at = FIXED_NOW
        self.subscription = subscription


class InMemoryProviderOperationRepository:
    def __init__(self) -> None:
        self.operations: dict[
            UUID,
            ProviderOperation,
        ] = {}

    def get_by_idempotency_key_for_update(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        operation_type: ProviderOperationType,
        idempotency_key: str,
    ) -> ProviderOperation | None:
        cast(FakeSession, session).touch()

        for operation in self.operations.values():
            if (
                operation.tenant_id == tenant_id
                and operation.operation_type is operation_type
                and operation.idempotency_key == idempotency_key
            ):
                return operation

        return None

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        provider_operation_id: UUID,
    ) -> ProviderOperation | None:
        cast(FakeSession, session).touch()
        return self.operations.get(provider_operation_id)

    def add_and_flush(
        self,
        session: Session,
        operation: ProviderOperation,
    ) -> None:
        cast(FakeSession, session).touch()
        operation.created_at = FIXED_NOW
        operation.updated_at = FIXED_NOW
        self.operations[operation.id] = operation

    def flush(self, session: Session) -> None:
        cast(FakeSession, session).touch()


class RecordingPaymentProvider:
    def __init__(
        self,
        session: FakeSession,
    ) -> None:
        self._session = session
        self.customer_calls = 0
        self.subscription_calls = 0

    @property
    def provider(self) -> BillingProvider:
        return BillingProvider.FAKE

    def create_customer(
        self,
        request: CreateCustomerRequest,
    ) -> CreateCustomerResult:
        assert not self._session.in_transaction()
        self.customer_calls += 1

        return CreateCustomerResult(
            provider_customer_id="fake_cus_customer",
            provider_reference="fake_op_customer",
        )

    def create_subscription(
        self,
        request: CreateSubscriptionRequest,
    ) -> CreateSubscriptionResult:
        assert not self._session.in_transaction()
        self.subscription_calls += 1

        return CreateSubscriptionResult(
            provider_subscription_id="fake_sub_subscription",
            provider_state_version=1,
            current_period_start=request.effective_at,
            current_period_end=PERIOD_END,
            provider_reference="fake_op_subscription",
        )


def _build_service(
    *,
    session: FakeSession,
    customer_repository: (InMemoryBillingCustomerRepository | None) = None,
    subscription_repository: (InMemorySubscriptionRepository | None) = None,
    operation_repository: (InMemoryProviderOperationRepository | None) = None,
    provider: RecordingPaymentProvider | None = None,
) -> tuple[
    CreateBillingSubscriptionService,
    InMemoryBillingCustomerRepository,
    InMemorySubscriptionRepository,
    InMemoryProviderOperationRepository,
    RecordingPaymentProvider,
]:
    resolved_customer_repository = customer_repository or InMemoryBillingCustomerRepository()
    resolved_subscription_repository = subscription_repository or InMemorySubscriptionRepository()
    resolved_operation_repository = operation_repository or InMemoryProviderOperationRepository()
    resolved_provider = provider or RecordingPaymentProvider(session)

    service = CreateBillingSubscriptionService(
        cast(PaymentProvider, resolved_provider),
        billing_customer_repository=cast(
            BillingCustomerRepository,
            resolved_customer_repository,
        ),
        subscription_repository=cast(
            SubscriptionRepository,
            resolved_subscription_repository,
        ),
        provider_operation_repository=cast(
            ProviderOperationRepository,
            resolved_operation_repository,
        ),
        clock=cast(Clock, FixedClock()),
    )

    return (
        service,
        resolved_customer_repository,
        resolved_subscription_repository,
        resolved_operation_repository,
        resolved_provider,
    )


def _command(
    *,
    price_code: str = "starter_monthly",
    idempotency_key: str = "request-key-123",
) -> CreateBillingSubscriptionCommand:
    return CreateBillingSubscriptionCommand(
        tenant_id=TENANT_ID,
        price_code=price_code,
        idempotency_key=idempotency_key,
    )


def test_service_creates_customer_and_subscription_across_commits() -> None:
    session = FakeSession()
    (
        service,
        customer_repository,
        subscription_repository,
        operation_repository,
        provider,
    ) = _build_service(session=session)

    result = service.execute(
        cast(Session, session),
        _command(),
    )

    assert session.commit_count == 3
    assert provider.customer_calls == 1
    assert provider.subscription_calls == 1
    assert customer_repository.customer is not None
    assert subscription_repository.subscription is not None
    assert result.status is SubscriptionStatus.ACTIVE
    assert result.replayed is False

    statuses = {
        operation.operation_type: operation.status
        for operation in operation_repository.operations.values()
    }
    assert statuses == {
        ProviderOperationType.CREATE_CUSTOMER: (ProviderOperationStatus.SUCCEEDED),
        ProviderOperationType.CREATE_SUBSCRIPTION: (ProviderOperationStatus.SUCCEEDED),
    }


def test_service_skips_customer_provider_call_when_link_exists() -> None:
    session = FakeSession()
    customer = BillingCustomer(
        id=uuid4(),
        tenant_id=TENANT_ID,
        provider=BillingProvider.FAKE,
        provider_customer_id="fake_cus_existing",
    )
    customer.created_at = FIXED_NOW
    customer.updated_at = FIXED_NOW
    (
        service,
        _,
        _,
        _,
        provider,
    ) = _build_service(
        session=session,
        customer_repository=(InMemoryBillingCustomerRepository(customer)),
    )

    result = service.execute(
        cast(Session, session),
        _command(),
    )

    assert session.commit_count == 2
    assert provider.customer_calls == 0
    assert provider.subscription_calls == 1
    assert result.replayed is False


def test_same_successful_key_replays_without_provider_calls() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        _,
        provider,
    ) = _build_service(session=session)
    command = _command()

    first = service.execute(
        cast(Session, session),
        command,
    )
    customer_calls = provider.customer_calls
    subscription_calls = provider.subscription_calls
    session.touch()

    replayed = service.execute(
        cast(Session, session),
        command,
    )

    assert first.id == replayed.id
    assert replayed.replayed is True
    assert provider.customer_calls == customer_calls
    assert provider.subscription_calls == subscription_calls


def test_same_key_with_different_price_is_rejected_before_provider() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        _,
        provider,
    ) = _build_service(session=session)

    service.execute(
        cast(Session, session),
        _command(),
    )
    customer_calls = provider.customer_calls
    subscription_calls = provider.subscription_calls
    session.touch()

    with pytest.raises(BillingIdempotencyConflictError):
        service.execute(
            cast(Session, session),
            _command(price_code="professional_monthly"),
        )

    assert provider.customer_calls == customer_calls
    assert provider.subscription_calls == subscription_calls


def test_new_key_is_rejected_when_subscription_exists() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        _,
        provider,
    ) = _build_service(session=session)

    service.execute(
        cast(Session, session),
        _command(),
    )
    customer_calls = provider.customer_calls
    subscription_calls = provider.subscription_calls
    session.touch()

    with pytest.raises(BillingSubscriptionAlreadyExistsError):
        service.execute(
            cast(Session, session),
            _command(idempotency_key="another-key"),
        )

    assert provider.customer_calls == customer_calls
    assert provider.subscription_calls == subscription_calls
