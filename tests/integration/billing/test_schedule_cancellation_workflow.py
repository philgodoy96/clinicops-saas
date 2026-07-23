from collections.abc import Iterator
from dataclasses import dataclass
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
    BillingInterval,
    BillingPlan,
    BillingProvider,
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
)
from clinicops.billing.models import (
    BillingCustomer,
    ProviderOperation,
    Subscription,
)
from clinicops.billing.providers.contracts import (
    ChangePlanRequest,
    CreateCustomerRequest,
    CreateSubscriptionRequest,
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
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
)
from clinicops.billing.services.schedule_cancellation import (
    ScheduleBillingSubscriptionCancellationCommand,
    ScheduleBillingSubscriptionCancellationService,
    ScheduledBillingSubscriptionCancellation,
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


@dataclass(frozen=True, slots=True)
class PersistedCancellationFixture:
    tenant_id: UUID
    subscription_id: UUID
    audit_user_id: UUID
    provider: FakePaymentProvider
    provider_subscription_id: str
    current_period_start: datetime
    current_period_end: datetime
    initial_provider_state_version: int


@pytest.fixture
def cancellation_fixture() -> Iterator[PersistedCancellationFixture]:
    fixture = _persist_subscription()

    try:
        yield fixture
    finally:
        _cleanup_tenants(fixture.tenant_id)
        _delete_user(fixture.audit_user_id)


def _persist_subscription(
    *,
    provider: FakePaymentProvider | None = None,
    pending_price_code: str | None = None,
) -> PersistedCancellationFixture:
    resolved_provider = provider or FakePaymentProvider()
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()
    audit_user_id = _persist_user(_create_user("cancellation-audit"))

    provider_customer = resolved_provider.create_customer(
        CreateCustomerRequest(provider_operation_key=(build_provider_operation_key(uuid4())))
    )
    provider_subscription = resolved_provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_customer_id=(provider_customer.provider_customer_id),
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )
    provider_state_version = provider_subscription.provider_state_version

    if pending_price_code is not None:
        plan_change = resolved_provider.change_plan(
            ChangePlanRequest(
                provider_operation_key=(build_provider_operation_key(uuid4())),
                provider_subscription_id=(provider_subscription.provider_subscription_id),
                target_price_code=pending_price_code,
                effective_at=(provider_subscription.current_period_end),
            )
        )
        provider_state_version = plan_change.provider_state_version

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Cancellation Clinic {tenant_id.hex}"),
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
                id=subscription_id,
                tenant_id=tenant_id,
                billing_customer_id=billing_customer_id,
                provider=BillingProvider.FAKE,
                provider_subscription_id=(provider_subscription.provider_subscription_id),
                price_code="starter_monthly",
                plan=BillingPlan.STARTER,
                billing_interval=(BillingInterval.MONTHLY),
                currency="USD",
                unit_amount=4900,
                pending_price_code=pending_price_code,
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=False,
                cancellation_requested_at=None,
                current_period_start=(provider_subscription.current_period_start),
                current_period_end=(provider_subscription.current_period_end),
                provider_state_version=(provider_state_version),
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return PersistedCancellationFixture(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        audit_user_id=audit_user_id,
        provider=resolved_provider,
        provider_subscription_id=(provider_subscription.provider_subscription_id),
        current_period_start=(provider_subscription.current_period_start),
        current_period_end=(provider_subscription.current_period_end),
        initial_provider_state_version=(provider_state_version),
    )


def _service(
    provider: FakePaymentProvider,
) -> ScheduleBillingSubscriptionCancellationService:
    return ScheduleBillingSubscriptionCancellationService(
        payment_provider=provider,
        clock=FixedClock(),
    )


def _command(
    fixture: PersistedCancellationFixture,
    *,
    idempotency_key: str = "cancellation-request-1",
    audit_context: AuditRecordingContext | None = None,
) -> ScheduleBillingSubscriptionCancellationCommand:
    return ScheduleBillingSubscriptionCancellationCommand(
        tenant_id=fixture.tenant_id,
        idempotency_key=idempotency_key,
        audit_context=audit_context or _audit_context(user_id=fixture.audit_user_id),
    )


def _execute(
    service: ScheduleBillingSubscriptionCancellationService,
    command: ScheduleBillingSubscriptionCancellationCommand,
) -> ScheduledBillingSubscriptionCancellation:
    with Session(get_engine()) as session:
        result = service.execute(
            session,
            command,
        )
        session.commit()
        return result


def _load_subscription(
    tenant_id: UUID,
) -> Subscription:
    with Session(get_engine()) as session:
        subscription = session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )

        assert subscription is not None
        return subscription


def _load_operations(
    tenant_id: UUID,
) -> list[ProviderOperation]:
    with Session(get_engine()) as session:
        return list(
            session.scalars(
                select(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id)
            ).all()
        )


def _cleanup_tenants(*tenant_ids: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id.in_(tenant_ids)))
        session.execute(
            delete(ProviderOperation).where(ProviderOperation.tenant_id.in_(tenant_ids))
        )
        session.execute(delete(Subscription).where(Subscription.tenant_id.in_(tenant_ids)))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id.in_(tenant_ids)))
        session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
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


def test_cancellation_persists_pending_state_and_operation(
    cancellation_fixture: PersistedCancellationFixture,
) -> None:
    service = _service(cancellation_fixture.provider)

    result = _execute(
        service,
        _command(cancellation_fixture),
    )

    subscription = _load_subscription(cancellation_fixture.tenant_id)
    operations = _load_operations(cancellation_fixture.tenant_id)

    assert result.replayed is False
    assert result.status is SubscriptionStatus.ACTIVE
    assert result.cancel_at_period_end is True
    assert result.cancellation_requested_at == FIXED_NOW
    assert result.canceled_at is None
    assert result.pending_price_code is None

    assert subscription.id == (cancellation_fixture.subscription_id)
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.price_code == "starter_monthly"
    assert subscription.plan is BillingPlan.STARTER
    assert subscription.billing_interval is BillingInterval.MONTHLY
    assert subscription.unit_amount == 4900
    assert subscription.cancel_at_period_end is True
    assert subscription.cancellation_requested_at == FIXED_NOW
    assert subscription.canceled_at is None
    assert subscription.pending_price_code is None
    assert subscription.current_period_start == cancellation_fixture.current_period_start
    assert subscription.current_period_end == cancellation_fixture.current_period_end
    assert subscription.provider_state_version == (
        cancellation_fixture.initial_provider_state_version + 1
    )

    assert len(operations) == 1
    operation = operations[0]

    assert operation.operation_type is ProviderOperationType.CANCEL_SUBSCRIPTION
    assert operation.status is ProviderOperationStatus.SUCCEEDED
    assert operation.attempt_count == 1
    assert operation.subscription_id == cancellation_fixture.subscription_id
    assert operation.result_payload is not None
    assert operation.result_payload["canceled_at"] == (
        cancellation_fixture.current_period_end.astimezone(UTC).isoformat()
    )


def test_cancellation_clears_pending_plan_change() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")

    try:
        result = _execute(
            _service(fixture.provider),
            _command(
                fixture,
                idempotency_key=("cancel-pending-plan-change"),
            ),
        )
        subscription = _load_subscription(fixture.tenant_id)

        assert result.cancel_at_period_end is True
        assert result.pending_price_code is None
        assert subscription.price_code == ("starter_monthly")
        assert subscription.pending_price_code is None
        assert subscription.provider_state_version == fixture.initial_provider_state_version + 1
    finally:
        _cleanup_tenants(fixture.tenant_id)
        _delete_user(fixture.audit_user_id)


def test_successful_cancellation_replays_without_duplicate_operation(
    cancellation_fixture: PersistedCancellationFixture,
) -> None:
    service = _service(cancellation_fixture.provider)
    command = _command(
        cancellation_fixture,
        idempotency_key="cancellation-replay",
    )

    first = _execute(service, command)
    replayed = _execute(service, command)

    assert replayed.id == first.id
    assert replayed.replayed is True
    assert replayed.cancel_at_period_end is True
    assert replayed.cancellation_requested_at == first.cancellation_requested_at

    operations = _load_operations(cancellation_fixture.tenant_id)

    assert len(operations) == 1
    assert operations[0].attempt_count == 1
    assert operations[0].status is ProviderOperationStatus.SUCCEEDED


def test_ambiguous_cancellation_recovers_with_same_provider_key() -> None:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)
    fixture = _persist_subscription(provider=provider)
    service = _service(provider)
    command = _command(
        fixture,
        idempotency_key="ambiguous-cancellation",
    )
    control.queue_outcome(
        operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
        outcome=FakeProviderOutcome.AMBIGUOUS_SUCCESS,
    )

    try:
        with pytest.raises(ProviderAmbiguousOutcomeError):
            _execute(service, command)

        failed_operation = _load_operations(fixture.tenant_id)[0]
        failed_subscription = _load_subscription(fixture.tenant_id)

        assert failed_operation.status is ProviderOperationStatus.FAILED_RETRYABLE
        assert failed_operation.attempt_count == 1
        assert failed_subscription.cancel_at_period_end is False
        assert failed_subscription.cancellation_requested_at is None
        assert failed_subscription.provider_state_version == fixture.initial_provider_state_version

        recovered = _execute(service, command)
        recovered_operation = _load_operations(fixture.tenant_id)[0]
        recovered_subscription = _load_subscription(fixture.tenant_id)

        assert recovered.replayed is False
        assert recovered.cancel_at_period_end is True
        assert recovered.cancellation_requested_at == (FIXED_NOW)
        assert recovered_operation.attempt_count == 2
        assert recovered_operation.status is ProviderOperationStatus.SUCCEEDED
        assert (
            recovered_subscription.provider_state_version
            == fixture.initial_provider_state_version + 1
        )
    finally:
        _cleanup_tenants(fixture.tenant_id)
        _delete_user(fixture.audit_user_id)


def test_terminal_cancellation_failure_is_persisted_and_not_retried() -> None:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)
    fixture = _persist_subscription(provider=provider)
    service = _service(provider)
    command = _command(
        fixture,
        idempotency_key="terminal-cancellation",
    )
    control.queue_outcome(
        operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
        outcome=FakeProviderOutcome.TERMINAL_REJECTION,
    )

    try:
        with pytest.raises(ProviderTerminalError):
            _execute(service, command)

        first_operation = _load_operations(fixture.tenant_id)[0]
        first_subscription = _load_subscription(fixture.tenant_id)

        assert first_operation.status is ProviderOperationStatus.FAILED_TERMINAL
        assert first_operation.attempt_count == 1
        assert first_subscription.cancel_at_period_end is False
        assert first_subscription.cancellation_requested_at is None

        with pytest.raises(ProviderTerminalError):
            _execute(service, command)

        replayed_operation = _load_operations(fixture.tenant_id)[0]

        assert replayed_operation.attempt_count == 1
        assert replayed_operation.status is ProviderOperationStatus.FAILED_TERMINAL
    finally:
        _cleanup_tenants(fixture.tenant_id)
        _delete_user(fixture.audit_user_id)


def test_cancellation_and_audit_commit_together(
    cancellation_fixture: PersistedCancellationFixture,
) -> None:
    context = _audit_context(user_id=cancellation_fixture.audit_user_id)
    idempotency_key = "audit-commit-cancellation"
    service = _service(cancellation_fixture.provider)
    command = _command(
        cancellation_fixture,
        idempotency_key=idempotency_key,
        audit_context=context,
    )

    with Session(get_engine()) as session:
        service.execute(session, command)
        session.commit()

    with Session(get_engine()) as verification_session:
        stored_subscription = verification_session.get(
            Subscription,
            cancellation_fixture.subscription_id,
        )
        stored_audit = verification_session.scalar(
            select(AuditLogEntry).where(
                AuditLogEntry.tenant_id == cancellation_fixture.tenant_id,
                AuditLogEntry.action == AuditAction.BILLING_SUBSCRIPTION_CANCELLED.value,
            )
        )

    assert stored_subscription is not None
    assert stored_subscription.cancel_at_period_end is True
    assert stored_audit is not None
    assert stored_audit.resource_type == AuditResourceType.SUBSCRIPTION.value
    assert stored_audit.resource_id == str(cancellation_fixture.subscription_id)
    assert stored_audit.actor_user_id == cancellation_fixture.audit_user_id
    assert stored_audit.actor_role == TenantRole.OWNER.value
    assert stored_audit.source == AuditSource.HTTP.value
    assert stored_audit.request_id == context.request_id
    assert stored_audit.correlation_id == context.correlation_id
    assert stored_audit.metadata_version == 1
    assert stored_audit.event_metadata == {
        "previous_status": SubscriptionStatus.ACTIVE.value,
        "new_status": SubscriptionStatus.CANCELED.value,
    }
    assert stored_audit.idempotency_key == (
        f"subscription-cancelled:{cancellation_fixture.subscription_id}:{idempotency_key}"
    )


def test_cancellation_caller_rollback_discards_pending_state_and_audit(
    cancellation_fixture: PersistedCancellationFixture,
) -> None:
    context = _audit_context(user_id=cancellation_fixture.audit_user_id)
    service = _service(cancellation_fixture.provider)
    command = _command(
        cancellation_fixture,
        idempotency_key="rollback-cancellation",
        audit_context=context,
    )

    with Session(get_engine()) as session:
        service.execute(session, command)
        session.rollback()

    subscription = _load_subscription(cancellation_fixture.tenant_id)

    assert subscription.cancel_at_period_end is False
    assert subscription.cancellation_requested_at is None
    assert (
        subscription.provider_state_version == cancellation_fixture.initial_provider_state_version
    )
    assert (
        _count_audit_rows(
            cancellation_fixture.tenant_id,
            action=AuditAction.BILLING_SUBSCRIPTION_CANCELLED.value,
        )
        == 0
    )


def test_audit_failure_prevents_cancellation_commit(
    cancellation_fixture: PersistedCancellationFixture,
) -> None:
    context = _audit_context(user_id=cancellation_fixture.audit_user_id)
    service = ScheduleBillingSubscriptionCancellationService(
        payment_provider=cancellation_fixture.provider,
        clock=FixedClock(),
        audit_recorder=FailingAuditRecorder(),
    )
    command = _command(
        cancellation_fixture,
        idempotency_key="audit-failure-cancellation",
        audit_context=context,
    )

    with Session(get_engine()) as session:
        with pytest.raises(SimulatedAuditRecordingError):
            service.execute(session, command)

        session.rollback()

    subscription = _load_subscription(cancellation_fixture.tenant_id)

    assert subscription.cancel_at_period_end is False
    assert subscription.cancellation_requested_at is None
    assert (
        subscription.provider_state_version == cancellation_fixture.initial_provider_state_version
    )
    assert (
        _count_audit_rows(
            cancellation_fixture.tenant_id,
            action=AuditAction.BILLING_SUBSCRIPTION_CANCELLED.value,
        )
        == 0
    )


def test_successful_cancellation_replay_emits_one_audit_row(
    cancellation_fixture: PersistedCancellationFixture,
) -> None:
    service = _service(cancellation_fixture.provider)
    command = _command(
        cancellation_fixture,
        idempotency_key="audit-replay-cancellation",
    )

    first = _execute(service, command)
    replayed = _execute(service, command)

    assert first.replayed is False
    assert replayed.replayed is True
    assert (
        _count_audit_rows(
            cancellation_fixture.tenant_id,
            action=AuditAction.BILLING_SUBSCRIPTION_CANCELLED.value,
        )
        == 1
    )
