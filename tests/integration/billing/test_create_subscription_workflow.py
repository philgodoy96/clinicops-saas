from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand, RecordedAuditLog
from clinicops.audit.enums import AuditSource
from clinicops.audit.models import AuditLogEntry
from clinicops.billing.enums import (
    BillingPlan,
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


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class RecordingAuditRecorder:
    """Capture audit commands without touching the database."""

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
            recorded_at=datetime.now(UTC),
        )


class FailingAuditRecorder:
    """Raise after the domain mutation has already been flushed."""

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError


def _audit_context(
    *,
    user_id: UUID,
) -> AuditRecordingContext:
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


@pytest.fixture
def audit_user_id() -> Iterator[UUID]:
    user_id = _persist_user(_create_user("billing-audit"))

    try:
        yield user_id
    finally:
        pass


@pytest.fixture
def tenant_id(audit_user_id: UUID) -> Iterator[UUID]:
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
        _delete_user(audit_user_id)


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
    audit_user_id: UUID,
    price_code: str = "starter_monthly",
    idempotency_key: str = "billing-request-123",
    audit_context: AuditRecordingContext | None = None,
) -> CreateBillingSubscriptionCommand:
    return CreateBillingSubscriptionCommand(
        tenant_id=tenant_id,
        price_code=price_code,
        idempotency_key=idempotency_key,
        audit_context=audit_context or _audit_context(user_id=audit_user_id),
    )


def _execute(
    service: CreateBillingSubscriptionService,
    command: CreateBillingSubscriptionCommand,
) -> CreatedBillingSubscription:
    with Session(get_engine()) as session:
        result = service.execute(
            session,
            command,
        )
        session.commit()
        return result


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
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id))
        session.execute(delete(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id))
        session.execute(delete(Subscription).where(Subscription.tenant_id == tenant_id))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def _count_audit_rows(
    tenant_id: UUID,
    *,
    action: str | None = None,
) -> int:
    with Session(get_engine()) as session:
        query = (
            select(func.count())
            .select_from(AuditLogEntry)
            .where(AuditLogEntry.tenant_id == tenant_id)
        )
        if action is not None:
            query = query.where(AuditLogEntry.action == action)

        return int(session.scalar(query) or 0)


def test_creation_persists_customer_subscription_and_operations(
    tenant_id: UUID,
    audit_user_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)

    result = _execute(
        service,
        _command(tenant_id, audit_user_id=audit_user_id),
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
    audit_user_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)
    command = _command(tenant_id, audit_user_id=audit_user_id)

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
    audit_user_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)
    idempotency_key = "billing-request-conflict"

    _execute(
        service,
        _command(
            tenant_id,
            audit_user_id=audit_user_id,
            idempotency_key=idempotency_key,
        ),
    )

    with pytest.raises(BillingIdempotencyConflictError):
        _execute(
            service,
            _command(
                tenant_id,
                audit_user_id=audit_user_id,
                price_code="professional_monthly",
                idempotency_key=idempotency_key,
            ),
        )


def test_existing_subscription_with_new_key_is_rejected(
    tenant_id: UUID,
    audit_user_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)

    _execute(
        service,
        _command(
            tenant_id,
            audit_user_id=audit_user_id,
            idempotency_key="first-request",
        ),
    )

    with pytest.raises(BillingSubscriptionAlreadyExistsError):
        _execute(
            service,
            _command(
                tenant_id,
                audit_user_id=audit_user_id,
                idempotency_key="second-request",
            ),
        )


def test_ambiguous_customer_result_resumes_without_duplication(
    tenant_id: UUID,
    audit_user_id: UUID,
) -> None:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)
    service = _build_service(provider)
    command = _command(
        tenant_id,
        audit_user_id=audit_user_id,
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
    audit_user_id: UUID,
) -> None:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)
    service = _build_service(provider)
    command = _command(
        tenant_id,
        audit_user_id=audit_user_id,
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


def test_create_subscription_and_audit_commit_together(
    tenant_id: UUID,
    audit_user_id: UUID,
) -> None:
    context = _audit_context(user_id=audit_user_id)
    provider = FakePaymentProvider()
    service = _build_service(provider)
    command = _command(
        tenant_id,
        audit_user_id=audit_user_id,
        idempotency_key="audit-commit-request",
        audit_context=context,
    )
    subscription_id: UUID | None = None

    with Session(get_engine()) as session:
        created = service.execute(session, command)
        subscription_id = created.id
        session.commit()

    with Session(get_engine()) as verification_session:
        stored_subscription = verification_session.get(Subscription, subscription_id)
        stored_audit = verification_session.scalar(
            select(AuditLogEntry).where(
                AuditLogEntry.tenant_id == tenant_id,
                AuditLogEntry.action == AuditAction.BILLING_SUBSCRIPTION_CREATED.value,
            )
        )

    assert stored_subscription is not None
    assert stored_audit is not None
    assert stored_audit.resource_type == AuditResourceType.SUBSCRIPTION.value
    assert stored_audit.resource_id == str(subscription_id)
    assert stored_audit.actor_user_id == audit_user_id
    assert stored_audit.actor_role == TenantRole.OWNER.value
    assert stored_audit.source == AuditSource.HTTP.value
    assert stored_audit.request_id == context.request_id
    assert stored_audit.correlation_id == context.correlation_id
    assert stored_audit.metadata_version == 1
    assert stored_audit.event_metadata == {
        "plan": BillingPlan.STARTER.value,
        "status": SubscriptionStatus.ACTIVE.value,
    }
    assert stored_audit.idempotency_key == f"subscription-created:{subscription_id}"


def test_create_subscription_caller_rollback_discards_subscription_and_audit(
    tenant_id: UUID,
    audit_user_id: UUID,
) -> None:
    context = _audit_context(user_id=audit_user_id)
    provider = FakePaymentProvider()
    service = _build_service(provider)
    command = _command(
        tenant_id,
        audit_user_id=audit_user_id,
        idempotency_key="rollback-request",
        audit_context=context,
    )

    with Session(get_engine()) as session:
        service.execute(session, command)
        session.rollback()

    with Session(get_engine()) as verification_session:
        stored_subscription = verification_session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        stored_audit = verification_session.scalar(
            select(AuditLogEntry).where(
                AuditLogEntry.tenant_id == tenant_id,
                AuditLogEntry.action == AuditAction.BILLING_SUBSCRIPTION_CREATED.value,
            )
        )

    assert stored_subscription is None
    assert stored_audit is None


def test_audit_failure_prevents_subscription_creation_commit(
    tenant_id: UUID,
    audit_user_id: UUID,
) -> None:
    context = _audit_context(user_id=audit_user_id)
    provider = FakePaymentProvider()
    service = CreateBillingSubscriptionService(
        payment_provider=provider,
        clock=FixedClock(),
        audit_recorder=FailingAuditRecorder(),
    )
    command = _command(
        tenant_id,
        audit_user_id=audit_user_id,
        idempotency_key="audit-failure-request",
        audit_context=context,
    )

    with Session(get_engine()) as session:
        with pytest.raises(SimulatedAuditRecordingError):
            service.execute(session, command)

        session.rollback()

    with Session(get_engine()) as verification_session:
        stored_subscription = verification_session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        stored_audit = verification_session.scalar(
            select(AuditLogEntry).where(
                AuditLogEntry.correlation_id == context.correlation_id,
                AuditLogEntry.action == AuditAction.BILLING_SUBSCRIPTION_CREATED.value,
            )
        )

    assert stored_subscription is None
    assert stored_audit is None


def test_successful_creation_replay_emits_one_audit_row(
    tenant_id: UUID,
    audit_user_id: UUID,
) -> None:
    provider = FakePaymentProvider()
    service = _build_service(provider)
    command = _command(
        tenant_id,
        audit_user_id=audit_user_id,
        idempotency_key="audit-replay-request",
    )

    first = _execute(service, command)
    replayed = _execute(service, command)

    assert first.replayed is False
    assert replayed.replayed is True
    assert (
        _count_audit_rows(
            tenant_id,
            action=AuditAction.BILLING_SUBSCRIPTION_CREATED.value,
        )
        == 1
    )
