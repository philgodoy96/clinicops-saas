from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand, RecordedAuditLog
from clinicops.audit.enums import AuditActorType, AuditSource
from clinicops.audit.models import AuditLogEntry
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    BillingWebhookProcessingOutcome,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventRetryableFailureError,
)
from clinicops.billing.models import (
    BillingCustomer,
    BillingWebhookEvent,
    Subscription,
)
from clinicops.billing.repositories import (
    BillingWebhookEventRepository,
)
from clinicops.billing.webhooks.process import (
    ProcessBillingWebhookEventCommand,
    ProcessBillingWebhookEventService,
    ProcessedBillingWebhookEvent,
)
from clinicops.db.session import get_engine
from clinicops.tenancy.models import Tenant

PERIOD_START = datetime(
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
PROCESSED_AT = datetime(
    2026,
    8,
    22,
    12,
    5,
    tzinfo=UTC,
)
CANCELLATION_REQUESTED_AT = datetime(
    2026,
    7,
    24,
    15,
    tzinfo=UTC,
)


class FixedClock:
    def now(self) -> datetime:
        return PROCESSED_AT


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class FailingAuditRecorder:
    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError("audit recording failed")


class FailOnCompletionWebhookEventRepository(BillingWebhookEventRepository):
    """Fail after Transaction B has flushed its dirty state."""

    def __init__(self) -> None:
        self.flush_count = 0

    def flush(self, session: Session) -> None:
        self.flush_count += 1

        if self.flush_count == 2:
            raise RuntimeError("Simulated completion persistence failure.")

        super().flush(session)


def _audit_context(
    *,
    correlation_id: str | None = None,
    request_id: str | None = "billing-webhook-processing-request",
) -> AuditRecordingContext:
    return AuditRecordingContext.worker_system(
        correlation_id=(
            correlation_id if correlation_id is not None else f"webhook-processing-{uuid4()}"
        ),
        request_id=request_id,
    )


def _command(
    webhook_event_id: UUID,
    *,
    audit_context: AuditRecordingContext | None = None,
) -> ProcessBillingWebhookEventCommand:
    return ProcessBillingWebhookEventCommand(
        webhook_event_id=webhook_event_id,
        audit_context=audit_context or _audit_context(),
    )


@dataclass(frozen=True, slots=True)
class PersistedSubscriptionFixture:
    tenant_id: UUID
    subscription_id: UUID
    provider_subscription_id: str


def _persist_subscription(
    *,
    pending_price_code: str | None = None,
    cancel_at_period_end: bool = False,
) -> PersistedSubscriptionFixture:
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()
    provider_subscription_id = f"fake_sub_{uuid4().hex}"

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Webhook Processing Clinic {tenant_id.hex}"),
            )
        )
        session.add(
            BillingCustomer(
                id=billing_customer_id,
                tenant_id=tenant_id,
                provider=BillingProvider.FAKE,
                provider_customer_id=(f"fake_customer_{uuid4().hex}"),
            )
        )
        session.add(
            Subscription(
                id=subscription_id,
                tenant_id=tenant_id,
                billing_customer_id=billing_customer_id,
                provider=BillingProvider.FAKE,
                provider_subscription_id=(provider_subscription_id),
                price_code="starter_monthly",
                plan=BillingPlan.STARTER,
                billing_interval=(BillingInterval.MONTHLY),
                currency="USD",
                unit_amount=4900,
                pending_price_code=pending_price_code,
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=(cancel_at_period_end),
                cancellation_requested_at=(
                    CANCELLATION_REQUESTED_AT if cancel_at_period_end else None
                ),
                current_period_start=PERIOD_START,
                current_period_end=PERIOD_END,
                provider_state_version=4,
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return PersistedSubscriptionFixture(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        provider_subscription_id=(provider_subscription_id),
    )


def _persist_renewal_event(
    *,
    provider_subscription_id: str,
    provider_state_version: int = 5,
    price_code: str = "starter_monthly",
) -> UUID:
    event_id = uuid4()
    provider_event_id = f"evt_{uuid4().hex}"
    payload = {
        "id": provider_event_id,
        "type": "subscription.renewed",
        "created_at": PERIOD_END.isoformat(),
        "data": {
            "provider_subscription_id": (provider_subscription_id),
            "provider_state_version": (provider_state_version),
            "price_code": price_code,
            "status": "active",
            "current_period_start": (PERIOD_END.isoformat()),
            "current_period_end": (NEXT_PERIOD_END.isoformat()),
            "canceled_at": None,
        },
    }

    _persist_event(
        event=BillingWebhookEvent(
            id=event_id,
            provider=BillingProvider.FAKE,
            provider_event_id=provider_event_id,
            event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
            provider_subscription_id=(provider_subscription_id),
            provider_created_at=PERIOD_END,
            provider_state_version=(provider_state_version),
            payload=payload,
            payload_sha256=sha256(provider_event_id.encode("utf-8")).hexdigest(),
            signature_timestamp=int(PERIOD_END.timestamp()),
            status=BillingWebhookEventStatus.RECEIVED,
            processing_attempt_count=0,
        )
    )

    return event_id


def _persist_cancellation_event(
    *,
    provider_subscription_id: str,
    provider_state_version: int = 5,
) -> UUID:
    event_id = uuid4()
    provider_event_id = f"evt_{uuid4().hex}"
    payload = {
        "id": provider_event_id,
        "type": "subscription.canceled",
        "created_at": PERIOD_END.isoformat(),
        "data": {
            "provider_subscription_id": (provider_subscription_id),
            "provider_state_version": (provider_state_version),
            "price_code": "starter_monthly",
            "status": "canceled",
            "current_period_start": (PERIOD_START.isoformat()),
            "current_period_end": (PERIOD_END.isoformat()),
            "canceled_at": PERIOD_END.isoformat(),
        },
    }

    _persist_event(
        event=BillingWebhookEvent(
            id=event_id,
            provider=BillingProvider.FAKE,
            provider_event_id=provider_event_id,
            event_type=(BillingWebhookEventType.SUBSCRIPTION_CANCELED.value),
            provider_subscription_id=(provider_subscription_id),
            provider_created_at=PERIOD_END,
            provider_state_version=(provider_state_version),
            payload=payload,
            payload_sha256=sha256(provider_event_id.encode("utf-8")).hexdigest(),
            signature_timestamp=int(PERIOD_END.timestamp()),
            status=BillingWebhookEventStatus.RECEIVED,
            processing_attempt_count=0,
        )
    )

    return event_id


def _persist_event(
    *,
    event: BillingWebhookEvent,
) -> None:
    with Session(get_engine()) as session:
        session.add(event)
        session.commit()


def _process(
    webhook_event_id: UUID,
    *,
    service: ProcessBillingWebhookEventService | None = None,
    audit_context: AuditRecordingContext | None = None,
) -> ProcessedBillingWebhookEvent:
    resolved_service = (
        service if service is not None else ProcessBillingWebhookEventService(clock=FixedClock())
    )

    with Session(get_engine()) as session:
        return resolved_service.execute(
            session,
            _command(
                webhook_event_id,
                audit_context=audit_context,
            ),
        )


def _count_audit_rows(
    tenant_id: UUID,
    *,
    action: str,
) -> int:
    with Session(get_engine()) as session:
        return len(
            list(
                session.scalars(
                    select(AuditLogEntry).where(
                        AuditLogEntry.tenant_id == tenant_id,
                        AuditLogEntry.action == action,
                    )
                ).all()
            )
        )


def _load_subscription(
    subscription_id: UUID,
) -> Subscription:
    with Session(get_engine()) as session:
        subscription = session.get(
            Subscription,
            subscription_id,
        )

        assert subscription is not None
        return subscription


def _load_event(
    webhook_event_id: UUID,
) -> BillingWebhookEvent:
    with Session(get_engine()) as session:
        event = session.get(
            BillingWebhookEvent,
            webhook_event_id,
        )

        assert event is not None
        return event


def _cleanup(
    *,
    tenant_id: UUID | None = None,
    webhook_event_ids: tuple[UUID, ...],
) -> None:
    with Session(get_engine()) as session:
        session.execute(
            delete(BillingWebhookEvent).where(BillingWebhookEvent.id.in_(webhook_event_ids))
        )

        if tenant_id is not None:
            session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id))
            session.execute(delete(Subscription).where(Subscription.tenant_id == tenant_id))
            session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.id == tenant_id))

        session.commit()


def test_renewal_applies_pending_plan_and_completes_event() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")
    event_id = _persist_renewal_event(
        provider_subscription_id=(fixture.provider_subscription_id),
        price_code="professional_monthly",
    )

    try:
        result = _process(event_id)
        subscription = _load_subscription(fixture.subscription_id)
        event = _load_event(event_id)

        assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
        assert result.subscription_id == (fixture.subscription_id)
        assert event.status is BillingWebhookEventStatus.PROCESSED
        assert event.processing_attempt_count == 1
        assert event.processed_at == PROCESSED_AT
        assert event.failure_code is None
        assert event.failure_message is None

        assert subscription.price_code == ("professional_monthly")
        assert subscription.plan is BillingPlan.PROFESSIONAL
        assert subscription.billing_interval is BillingInterval.MONTHLY
        assert subscription.currency == "USD"
        assert subscription.unit_amount == 9900
        assert subscription.pending_price_code is None
        assert subscription.status is SubscriptionStatus.ACTIVE
        assert subscription.current_period_start == PERIOD_END
        assert subscription.current_period_end == NEXT_PERIOD_END
        assert subscription.provider_state_version == 5
        assert subscription.last_provider_event_at == PERIOD_END
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )


def test_cancellation_finalizes_scheduled_state_atomically() -> None:
    fixture = _persist_subscription(
        pending_price_code="professional_monthly",
        cancel_at_period_end=True,
    )
    event_id = _persist_cancellation_event(
        provider_subscription_id=(fixture.provider_subscription_id)
    )

    try:
        result = _process(event_id)
        subscription = _load_subscription(fixture.subscription_id)
        event = _load_event(event_id)

        assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
        assert event.status is BillingWebhookEventStatus.PROCESSED
        assert event.processing_attempt_count == 1
        assert event.processed_at == PROCESSED_AT

        assert subscription.status is SubscriptionStatus.CANCELED
        assert subscription.canceled_at == PERIOD_END
        assert subscription.cancel_at_period_end is False
        assert subscription.cancellation_requested_at == (CANCELLATION_REQUESTED_AT)
        assert subscription.pending_price_code is None
        assert subscription.provider_state_version == 5
        assert subscription.last_provider_event_at == PERIOD_END
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )


def test_unknown_subscription_is_persisted_as_retryable() -> None:
    event_id = _persist_renewal_event(provider_subscription_id=(f"fake_sub_unknown_{uuid4().hex}"))

    try:
        with pytest.raises(BillingWebhookEventRetryableFailureError) as exception_info:
            _process(event_id)

        event = _load_event(event_id)

        assert exception_info.value.failure_code == "billing_webhook_subscription_not_found"
        assert event.status is BillingWebhookEventStatus.FAILED_RETRYABLE
        assert event.processing_attempt_count == 1
        assert event.processed_at is None
        assert event.failure_code == ("billing_webhook_subscription_not_found")
        assert event.failure_message is not None
    finally:
        _cleanup(
            webhook_event_ids=(event_id,),
        )


def test_stale_event_is_ignored_without_local_mutation() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")
    event_id = _persist_renewal_event(
        provider_subscription_id=(fixture.provider_subscription_id),
        provider_state_version=4,
        price_code="professional_monthly",
    )

    try:
        result = _process(event_id)
        subscription = _load_subscription(fixture.subscription_id)
        event = _load_event(event_id)

        assert result.outcome is BillingWebhookProcessingOutcome.IGNORED
        assert event.status is BillingWebhookEventStatus.IGNORED
        assert event.processing_attempt_count == 1
        assert event.processed_at == PROCESSED_AT

        assert subscription.price_code == "starter_monthly"
        assert subscription.pending_price_code == ("professional_monthly")
        assert subscription.current_period_start == (PERIOD_START)
        assert subscription.current_period_end == PERIOD_END
        assert subscription.provider_state_version == 4
        assert subscription.last_provider_event_at is None
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )


def test_completion_failure_rolls_back_subscription_and_event_together() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")
    event_id = _persist_renewal_event(
        provider_subscription_id=(fixture.provider_subscription_id),
        price_code="professional_monthly",
    )
    repository = FailOnCompletionWebhookEventRepository()
    service = ProcessBillingWebhookEventService(
        webhook_event_repository=repository,
        clock=FixedClock(),
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="Simulated completion",
        ):
            _process(
                event_id,
                service=service,
            )

        subscription = _load_subscription(fixture.subscription_id)
        event = _load_event(event_id)

        assert repository.flush_count == 2

        assert event.status is BillingWebhookEventStatus.PROCESSING
        assert event.processing_attempt_count == 1
        assert event.processed_at is None
        assert event.failure_code is None

        assert subscription.price_code == "starter_monthly"
        assert subscription.plan is BillingPlan.STARTER
        assert subscription.unit_amount == 4900
        assert subscription.pending_price_code == ("professional_monthly")
        assert subscription.current_period_start == (PERIOD_START)
        assert subscription.current_period_end == PERIOD_END
        assert subscription.provider_state_version == 4
        assert subscription.last_provider_event_at is None
        assert (
            _count_audit_rows(
                fixture.tenant_id,
                action=AuditAction.BILLING_WEBHOOK_PROCESSED.value,
            )
            == 0
        )
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )


def test_processed_webhook_and_audit_commit_together() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")
    event_id = _persist_renewal_event(
        provider_subscription_id=(fixture.provider_subscription_id),
        price_code="professional_monthly",
    )
    context = _audit_context(
        correlation_id=f"processed-audit-{uuid4()}",
        request_id="processed-webhook-request",
    )

    try:
        result = _process(event_id, audit_context=context)
        subscription = _load_subscription(fixture.subscription_id)
        event = _load_event(event_id)

        with Session(get_engine()) as verification_session:
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == fixture.tenant_id,
                    AuditLogEntry.action == AuditAction.BILLING_WEBHOOK_PROCESSED.value,
                )
            )

        assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
        assert event.status is BillingWebhookEventStatus.PROCESSED
        assert subscription.price_code == "professional_monthly"
        assert stored_audit is not None
        assert stored_audit.resource_type == (AuditResourceType.BILLING_WEBHOOK_EVENT.value)
        assert stored_audit.resource_id == str(event_id)
        assert stored_audit.actor_type == AuditActorType.SYSTEM.value
        assert stored_audit.actor_user_id is None
        assert stored_audit.actor_role is None
        assert stored_audit.source == AuditSource.WORKER.value
        assert stored_audit.request_id == context.request_id
        assert stored_audit.correlation_id == context.correlation_id
        assert stored_audit.metadata_version == 1
        assert stored_audit.event_metadata == {
            "event_type": BillingWebhookEventType.SUBSCRIPTION_RENEWED.value,
            "processing_outcome": "processed",
        }
        assert stored_audit.idempotency_key == (f"billing-webhook-audit:{event_id}:processed")
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )


def test_ignored_webhook_and_audit_commit_together() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")
    event_id = _persist_renewal_event(
        provider_subscription_id=(fixture.provider_subscription_id),
        provider_state_version=4,
        price_code="professional_monthly",
    )
    context = _audit_context(correlation_id=f"ignored-audit-{uuid4()}")

    try:
        result = _process(event_id, audit_context=context)

        with Session(get_engine()) as verification_session:
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == fixture.tenant_id,
                    AuditLogEntry.action == AuditAction.BILLING_WEBHOOK_IGNORED.value,
                )
            )
            processed_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == fixture.tenant_id,
                    AuditLogEntry.action == AuditAction.BILLING_WEBHOOK_PROCESSED.value,
                )
            )

        assert result.outcome is BillingWebhookProcessingOutcome.IGNORED
        assert stored_audit is not None
        assert stored_audit.event_metadata == {
            "event_type": BillingWebhookEventType.SUBSCRIPTION_RENEWED.value,
            "processing_outcome": "ignored",
        }
        assert stored_audit.idempotency_key == (f"billing-webhook-audit:{event_id}:ignored")
        assert processed_audit is None
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )


def test_audit_failure_prevents_billing_and_webhook_commit() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")
    event_id = _persist_renewal_event(
        provider_subscription_id=(fixture.provider_subscription_id),
        price_code="professional_monthly",
    )
    service = ProcessBillingWebhookEventService(
        clock=FixedClock(),
        audit_recorder=FailingAuditRecorder(),
    )

    try:
        with pytest.raises(SimulatedAuditRecordingError):
            _process(event_id, service=service)

        subscription = _load_subscription(fixture.subscription_id)
        event = _load_event(event_id)

        assert event.status is BillingWebhookEventStatus.PROCESSING
        assert event.processing_attempt_count == 1
        assert event.processed_at is None
        assert subscription.price_code == "starter_monthly"
        assert subscription.provider_state_version == 4
        assert (
            _count_audit_rows(
                fixture.tenant_id,
                action=AuditAction.BILLING_WEBHOOK_PROCESSED.value,
            )
            == 0
        )
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )


def test_successful_processed_replay_emits_one_audit_row() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")
    event_id = _persist_renewal_event(
        provider_subscription_id=(fixture.provider_subscription_id),
        price_code="professional_monthly",
    )
    context = _audit_context(correlation_id=f"replay-processed-{uuid4()}")

    try:
        first = _process(event_id, audit_context=context)
        replayed = _process(event_id, audit_context=context)

        assert first.outcome is BillingWebhookProcessingOutcome.APPLIED
        assert replayed.outcome is BillingWebhookProcessingOutcome.APPLIED
        assert (
            _count_audit_rows(
                fixture.tenant_id,
                action=AuditAction.BILLING_WEBHOOK_PROCESSED.value,
            )
            == 1
        )
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )


def test_successful_ignored_replay_emits_one_audit_row() -> None:
    fixture = _persist_subscription(pending_price_code="professional_monthly")
    event_id = _persist_renewal_event(
        provider_subscription_id=(fixture.provider_subscription_id),
        provider_state_version=4,
        price_code="professional_monthly",
    )
    context = _audit_context(correlation_id=f"replay-ignored-{uuid4()}")

    try:
        first = _process(event_id, audit_context=context)
        replayed = _process(event_id, audit_context=context)

        assert first.outcome is BillingWebhookProcessingOutcome.IGNORED
        assert replayed.outcome is BillingWebhookProcessingOutcome.IGNORED
        assert (
            _count_audit_rows(
                fixture.tenant_id,
                action=AuditAction.BILLING_WEBHOOK_IGNORED.value,
            )
            == 1
        )
        assert (
            _count_audit_rows(
                fixture.tenant_id,
                action=AuditAction.BILLING_WEBHOOK_PROCESSED.value,
            )
            == 0
        )
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )
