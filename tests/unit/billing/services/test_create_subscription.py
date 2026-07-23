from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import (
    RecordAuditLogCommand,
    RecordedAuditLog,
)
from clinicops.audit.enums import AuditSource
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
from clinicops.tenancy.models import TenantRole

ACTOR_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
REQUEST_ID = "billing-create-request-id"
CORRELATION_ID = "billing-create-correlation-id"


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class RecordingAuditRecorder:
    def __init__(self) -> None:
        self.sessions: list[Session] = []
        self.commands: list[RecordAuditLogCommand] = []

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        self.sessions.append(session)
        self.commands.append(command)

        return RecordedAuditLog(
            audit_log_id=uuid4(),
            created=True,
            recorded_at=FIXED_NOW,
        )


class FailingAuditRecorder:
    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError("audit recording failed")


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


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=ACTOR_USER_ID,
        role=TenantRole.OWNER.value,
        request_id=REQUEST_ID,
        correlation_id=CORRELATION_ID,
    )


def _build_service(
    *,
    session: FakeSession,
    customer_repository: (InMemoryBillingCustomerRepository | None) = None,
    subscription_repository: (InMemorySubscriptionRepository | None) = None,
    operation_repository: (InMemoryProviderOperationRepository | None) = None,
    provider: RecordingPaymentProvider | None = None,
    audit_recorder: RecordingAuditRecorder | FailingAuditRecorder | None = None,
) -> tuple[
    CreateBillingSubscriptionService,
    InMemoryBillingCustomerRepository,
    InMemorySubscriptionRepository,
    InMemoryProviderOperationRepository,
    RecordingPaymentProvider,
    RecordingAuditRecorder | FailingAuditRecorder,
]:
    resolved_customer_repository = customer_repository or InMemoryBillingCustomerRepository()
    resolved_subscription_repository = subscription_repository or InMemorySubscriptionRepository()
    resolved_operation_repository = operation_repository or InMemoryProviderOperationRepository()
    resolved_provider = provider or RecordingPaymentProvider(session)
    resolved_audit_recorder = audit_recorder or RecordingAuditRecorder()

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
        audit_recorder=resolved_audit_recorder,
    )

    return (
        service,
        resolved_customer_repository,
        resolved_subscription_repository,
        resolved_operation_repository,
        resolved_provider,
        resolved_audit_recorder,
    )


def _command(
    *,
    price_code: str = "starter_monthly",
    idempotency_key: str = "request-key-123",
    audit_context: AuditRecordingContext | None = None,
) -> CreateBillingSubscriptionCommand:
    return CreateBillingSubscriptionCommand(
        tenant_id=TENANT_ID,
        price_code=price_code,
        idempotency_key=idempotency_key,
        audit_context=audit_context or _audit_context(),
    )


def _assert_safe_creation_metadata(metadata: Mapping[str, object]) -> None:
    assert metadata == {
        "plan": "starter",
        "status": "active",
    }
    forbidden = {
        "provider_customer_id",
        "provider_subscription_id",
        "provider_reference",
        "request_payload",
        "result_payload",
        "api_key",
        "secret",
        "authorization",
        "idempotency_key",
    }
    assert forbidden.isdisjoint(metadata)


def test_service_creates_customer_and_subscription_across_commits() -> None:
    session = FakeSession()
    (
        service,
        customer_repository,
        subscription_repository,
        operation_repository,
        provider,
        recorder,
    ) = _build_service(session=session)

    result = service.execute(
        cast(Session, session),
        _command(),
    )

    assert session.commit_count == 2
    assert session.rollback_count == 0
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

    assert isinstance(recorder, RecordingAuditRecorder)
    assert recorder.sessions == [cast(Session, session)]
    assert len(recorder.commands) == 1
    command = recorder.commands[0]
    assert command.action == AuditAction.BILLING_SUBSCRIPTION_CREATED.value
    assert command.resource_type == AuditResourceType.SUBSCRIPTION.value
    assert command.resource_id == str(result.id)
    assert command.tenant_id == TENANT_ID
    assert command.actor.user_id == ACTOR_USER_ID
    assert command.actor.role == TenantRole.OWNER.value
    assert command.source is AuditSource.HTTP
    assert command.request_id == REQUEST_ID
    assert command.correlation_id == CORRELATION_ID
    assert command.metadata_version == 1
    assert command.idempotency_key == f"subscription-created:{result.id}"
    _assert_safe_creation_metadata(command.metadata)


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
        recorder,
    ) = _build_service(
        session=session,
        customer_repository=(InMemoryBillingCustomerRepository(customer)),
    )

    result = service.execute(
        cast(Session, session),
        _command(),
    )

    assert session.commit_count == 1
    assert provider.customer_calls == 0
    assert provider.subscription_calls == 1
    assert result.replayed is False
    assert isinstance(recorder, RecordingAuditRecorder)
    assert len(recorder.commands) == 1


def test_same_successful_key_replays_without_provider_calls() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        _,
        provider,
        recorder,
    ) = _build_service(session=session)
    command = _command()

    first = service.execute(
        cast(Session, session),
        command,
    )
    assert isinstance(recorder, RecordingAuditRecorder)
    customer_calls = provider.customer_calls
    subscription_calls = provider.subscription_calls
    creation_commands = len(recorder.commands)
    session.touch()

    replayed = service.execute(
        cast(Session, session),
        command,
    )

    assert first.id == replayed.id
    assert replayed.replayed is True
    assert provider.customer_calls == customer_calls
    assert provider.subscription_calls == subscription_calls
    assert len(recorder.commands) == creation_commands


def test_same_key_with_different_price_is_rejected_before_provider() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        _,
        provider,
        recorder,
    ) = _build_service(session=session)

    service.execute(
        cast(Session, session),
        _command(),
    )
    assert isinstance(recorder, RecordingAuditRecorder)
    customer_calls = provider.customer_calls
    subscription_calls = provider.subscription_calls
    creation_commands = len(recorder.commands)
    session.touch()

    with pytest.raises(BillingIdempotencyConflictError):
        service.execute(
            cast(Session, session),
            _command(price_code="professional_monthly"),
        )

    assert provider.customer_calls == customer_calls
    assert provider.subscription_calls == subscription_calls
    assert len(recorder.commands) == creation_commands


def test_new_key_is_rejected_when_subscription_exists() -> None:
    session = FakeSession()
    (
        service,
        _,
        _,
        _,
        provider,
        recorder,
    ) = _build_service(session=session)

    service.execute(
        cast(Session, session),
        _command(),
    )
    assert isinstance(recorder, RecordingAuditRecorder)
    customer_calls = provider.customer_calls
    subscription_calls = provider.subscription_calls
    creation_commands = len(recorder.commands)
    session.touch()

    with pytest.raises(BillingSubscriptionAlreadyExistsError):
        service.execute(
            cast(Session, session),
            _command(idempotency_key="another-key"),
        )

    assert provider.customer_calls == customer_calls
    assert provider.subscription_calls == subscription_calls
    assert len(recorder.commands) == creation_commands


def test_audit_failure_propagates_without_final_commit() -> None:
    session = FakeSession()
    (
        service,
        _,
        subscription_repository,
        _,
        provider,
        _,
    ) = _build_service(
        session=session,
        audit_recorder=FailingAuditRecorder(),
    )

    with pytest.raises(SimulatedAuditRecordingError):
        service.execute(
            cast(Session, session),
            _command(),
        )

    assert provider.subscription_calls == 1
    assert session.commit_count == 2
    assert session.rollback_count == 1
    assert subscription_repository.subscription is not None
