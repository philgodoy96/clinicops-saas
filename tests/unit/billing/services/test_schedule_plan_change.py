from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
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
    BillingPlanChangeSamePriceError,
    BillingSubscriptionCancellationPendingError,
    BillingSubscriptionNotActiveError,
    BillingSubscriptionNotFoundError,
)
from clinicops.billing.models import (
    ProviderOperation,
    Subscription,
)
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.providers.contracts import (
    ChangePlanRequest,
    ChangePlanResult,
)
from clinicops.billing.providers.exceptions import (
    ProviderRetryableError,
    ProviderTerminalError,
)
from clinicops.billing.repositories.provider_operation_repository import (
    ProviderOperationRepository,
)
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
)
from clinicops.billing.services.schedule_plan_change import (
    ScheduleBillingPlanChangeCommand,
    ScheduleBillingPlanChangeService,
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
NEXT_PERIOD_END = datetime(
    2026,
    9,
    22,
    12,
    tzinfo=UTC,
)
TENANT_ID = UUID("5c9d9f0b-bffd-4519-85e4-817f365daee8")


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self._in_transaction = True

    def commit(self) -> None:
        self.commit_count += 1
        self._in_transaction = False

    def rollback(self) -> None:
        self.rollback_count += 1
        self._in_transaction = False

    def in_transaction(self) -> bool:
        return self._in_transaction

    def touch(self) -> None:
        self._in_transaction = True


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


class InMemorySubscriptionRepository:
    def __init__(
        self,
        subscription: Subscription | None,
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

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        subscription_id: UUID,
    ) -> Subscription | None:
        cast(FakeSession, session).touch()

        if self.subscription is not None and self.subscription.id == subscription_id:
            return self.subscription

        return None

    def flush(self, session: Session) -> None:
        cast(FakeSession, session).touch()
        assert self.subscription is not None
        self.subscription.updated_at = FIXED_NOW


class InMemoryProviderOperationRepository:
    def __init__(self) -> None:
        self.operations: dict[UUID, ProviderOperation] = {}

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


class RecordingPlanChangeProvider:
    def __init__(
        self,
        session: FakeSession,
        *,
        failures: list[ProviderRetryableError | ProviderTerminalError] | None = None,
    ) -> None:
        self._session = session
        self._failures = list(failures or [])
        self.calls = 0
        self.requests: list[ChangePlanRequest] = []

    @property
    def provider(self) -> BillingProvider:
        return BillingProvider.FAKE

    def change_plan(
        self,
        request: ChangePlanRequest,
    ) -> ChangePlanResult:
        assert not self._session.in_transaction()
        self.calls += 1
        self.requests.append(request)

        if self._failures:
            raise self._failures.pop(0)

        return ChangePlanResult(
            provider_subscription_id=(request.provider_subscription_id),
            provider_state_version=2,
            effective_price_code=(request.target_price_code),
            current_period_start=request.effective_at,
            current_period_end=NEXT_PERIOD_END,
            provider_reference="fake_op_plan_change",
        )


def _subscription(
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    price_code: str = "starter_monthly",
    pending_price_code: str | None = None,
    cancel_at_period_end: bool = False,
) -> Subscription:
    subscription = Subscription(
        id=uuid4(),
        tenant_id=TENANT_ID,
        billing_customer_id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_subscription_id="fake_sub_existing",
        price_code=price_code,
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=4900,
        pending_price_code=pending_price_code,
        status=status,
        cancel_at_period_end=cancel_at_period_end,
        cancellation_requested_at=(FIXED_NOW if cancel_at_period_end else None),
        current_period_start=FIXED_NOW,
        current_period_end=PERIOD_END,
        provider_state_version=1,
        last_provider_event_at=None,
        canceled_at=None,
    )
    subscription.created_at = FIXED_NOW
    subscription.updated_at = FIXED_NOW

    return subscription


def _build_service(
    *,
    session: FakeSession,
    subscription: Subscription | None = None,
    provider: RecordingPlanChangeProvider | None = None,
) -> tuple[
    ScheduleBillingPlanChangeService,
    InMemorySubscriptionRepository,
    InMemoryProviderOperationRepository,
    RecordingPlanChangeProvider,
]:
    subscription_repository = InMemorySubscriptionRepository(subscription)
    operation_repository = InMemoryProviderOperationRepository()
    resolved_provider = provider or RecordingPlanChangeProvider(session)
    service = ScheduleBillingPlanChangeService(
        cast(PaymentProvider, resolved_provider),
        subscription_repository=cast(
            SubscriptionRepository,
            subscription_repository,
        ),
        provider_operation_repository=cast(
            ProviderOperationRepository,
            operation_repository,
        ),
        clock=cast(Clock, FixedClock()),
    )

    return (
        service,
        subscription_repository,
        operation_repository,
        resolved_provider,
    )


def _command(
    *,
    target_price_code: str = "professional_monthly",
    idempotency_key: str = "plan-change-request-1",
) -> ScheduleBillingPlanChangeCommand:
    return ScheduleBillingPlanChangeCommand(
        tenant_id=TENANT_ID,
        target_price_code=target_price_code,
        idempotency_key=idempotency_key,
    )


def test_service_schedules_plan_change_across_commits() -> None:
    session = FakeSession()
    subscription = _subscription()
    (
        service,
        subscription_repository,
        operation_repository,
        provider,
    ) = _build_service(
        session=session,
        subscription=subscription,
    )

    result = service.execute(
        cast(Session, session),
        _command(),
    )

    assert session.commit_count == 2
    assert provider.calls == 1
    assert provider.requests[0].effective_at == PERIOD_END
    assert subscription.price_code == "starter_monthly"
    assert subscription.pending_price_code == "professional_monthly"
    assert subscription.current_period_end == PERIOD_END
    assert subscription.provider_state_version == 2
    assert subscription_repository.subscription is subscription
    assert result.price_code == "starter_monthly"
    assert result.pending_price_code == "professional_monthly"
    assert result.replayed is False

    operation = next(iter(operation_repository.operations.values()))
    assert operation.operation_type is ProviderOperationType.CHANGE_PLAN
    assert operation.status is ProviderOperationStatus.SUCCEEDED
    assert operation.attempt_count == 1
    assert operation.subscription_id == subscription.id


def test_same_successful_key_replays_without_provider_call() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        provider,
    ) = _build_service(
        session=session,
        subscription=_subscription(),
    )
    command = _command()

    first = service.execute(
        cast(Session, session),
        command,
    )
    session.touch()

    replayed = service.execute(
        cast(Session, session),
        command,
    )

    assert first.id == replayed.id
    assert replayed.replayed is True
    assert provider.calls == 1


def test_same_key_with_different_target_is_rejected() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        provider,
    ) = _build_service(
        session=session,
        subscription=_subscription(),
    )

    service.execute(
        cast(Session, session),
        _command(),
    )
    session.touch()

    with pytest.raises(BillingIdempotencyConflictError):
        service.execute(
            cast(Session, session),
            _command(
                target_price_code="starter_yearly",
            ),
        )

    assert provider.calls == 1


def test_new_key_is_rejected_when_plan_change_is_pending() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        provider,
    ) = _build_service(
        session=session,
        subscription=_subscription(),
    )

    service.execute(
        cast(Session, session),
        _command(),
    )
    session.touch()

    with pytest.raises(BillingPlanChangeAlreadyPendingError):
        service.execute(
            cast(Session, session),
            _command(
                idempotency_key="another-plan-change-key",
            ),
        )

    assert provider.calls == 1


@pytest.mark.parametrize(
    ("subscription", "error_type"),
    [
        (
            None,
            BillingSubscriptionNotFoundError,
        ),
        (
            _subscription(price_code="professional_monthly"),
            BillingPlanChangeSamePriceError,
        ),
        (
            _subscription(status=SubscriptionStatus.PAST_DUE),
            BillingSubscriptionNotActiveError,
        ),
        (
            _subscription(cancel_at_period_end=True),
            BillingSubscriptionCancellationPendingError,
        ),
        (
            _subscription(pending_price_code="starter_yearly"),
            BillingPlanChangeAlreadyPendingError,
        ),
    ],
)
def test_business_conflicts_skip_provider(
    subscription: Subscription | None,
    error_type: type[Exception],
) -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        provider,
    ) = _build_service(
        session=session,
        subscription=subscription,
    )

    with pytest.raises(error_type):
        service.execute(
            cast(Session, session),
            _command(),
        )

    assert provider.calls == 0


def test_retryable_failure_is_persisted_and_can_resume() -> None:
    session = FakeSession()
    provider = RecordingPlanChangeProvider(
        session,
        failures=[
            ProviderRetryableError(
                provider=BillingProvider.FAKE,
                operation_type=(ProviderOperationType.CHANGE_PLAN),
                internal_message="temporary provider failure",
            )
        ],
    )
    (
        service,
        subscription_repository,
        operation_repository,
        _,
    ) = _build_service(
        session=session,
        subscription=_subscription(),
        provider=provider,
    )
    command = _command()

    with pytest.raises(ProviderRetryableError):
        service.execute(
            cast(Session, session),
            command,
        )

    operation = next(iter(operation_repository.operations.values()))
    assert operation.status is ProviderOperationStatus.FAILED_RETRYABLE
    assert operation.attempt_count == 1
    assert operation.completed_at is None
    assert subscription_repository.subscription is not None
    assert subscription_repository.subscription.pending_price_code is None

    session.touch()
    recovered = service.execute(
        cast(Session, session),
        command,
    )

    assert provider.calls == 2
    resumed_operation = next(iter(operation_repository.operations.values()))
    assert resumed_operation.attempt_count == 2
    assert resumed_operation.status is ProviderOperationStatus.SUCCEEDED
    assert recovered.pending_price_code == "professional_monthly"


def test_terminal_failure_is_persisted_and_not_retried() -> None:
    session = FakeSession()
    provider = RecordingPlanChangeProvider(
        session,
        failures=[
            ProviderTerminalError(
                provider=BillingProvider.FAKE,
                operation_type=(ProviderOperationType.CHANGE_PLAN),
                internal_message="terminal provider rejection",
            )
        ],
    )
    (
        service,
        _,
        operation_repository,
        _,
    ) = _build_service(
        session=session,
        subscription=_subscription(),
        provider=provider,
    )
    command = _command()

    with pytest.raises(ProviderTerminalError):
        service.execute(
            cast(Session, session),
            command,
        )

    operation = next(iter(operation_repository.operations.values()))
    assert operation.status is ProviderOperationStatus.FAILED_TERMINAL
    assert operation.completed_at == FIXED_NOW

    session.touch()

    with pytest.raises(ProviderTerminalError):
        service.execute(
            cast(Session, session),
            command,
        )

    assert provider.calls == 1
