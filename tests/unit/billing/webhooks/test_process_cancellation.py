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
    BillingInterval,
    BillingPlan,
    BillingProvider,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    BillingWebhookProcessingOutcome,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventTerminalFailureError,
)
from clinicops.billing.models import (
    BillingWebhookEvent,
    Subscription,
)
from clinicops.billing.repositories import (
    BillingWebhookEventRepository,
    SubscriptionRepository,
)
from clinicops.billing.webhooks.process import (
    ProcessBillingWebhookEventCommand,
    ProcessBillingWebhookEventService,
)

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
REQUESTED_AT = datetime(
    2026,
    7,
    24,
    15,
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


class FixedClock:
    def now(self) -> datetime:
        return PROCESSED_AT


class RecordingSession:
    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        pass


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
            recorded_at=PROCESSED_AT,
        )


class RecordingWebhookRepository:
    def __init__(
        self,
        event: BillingWebhookEvent,
    ) -> None:
        self.event = event
        self.lock_count = 0
        self.flush_count = 0

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        webhook_event_id: UUID,
    ) -> BillingWebhookEvent | None:
        self.lock_count += 1

        if self.event.id == webhook_event_id:
            return self.event

        return None

    def flush(self, session: Session) -> None:
        self.flush_count += 1


class RecordingSubscriptionRepository:
    def __init__(
        self,
        subscription: Subscription,
    ) -> None:
        self.subscription = subscription
        self.flush_count = 0

    def get_by_provider_subscription_id_for_update(
        self,
        session: Session,
        *,
        provider: BillingProvider,
        provider_subscription_id: str,
    ) -> Subscription | None:
        if (
            self.subscription.provider is provider
            and self.subscription.provider_subscription_id == provider_subscription_id
        ):
            return self.subscription

        return None

    def flush(self, session: Session) -> None:
        self.flush_count += 1


def _subscription(
    *,
    provider_state_version: int = 4,
    cancel_at_period_end: bool = True,
) -> Subscription:
    return Subscription(
        id=uuid4(),
        tenant_id=uuid4(),
        billing_customer_id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_subscription_id="fake_sub_01",
        price_code="starter_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=4900,
        pending_price_code=None,
        status=SubscriptionStatus.ACTIVE,
        cancel_at_period_end=cancel_at_period_end,
        cancellation_requested_at=(REQUESTED_AT if cancel_at_period_end else None),
        current_period_start=PERIOD_START,
        current_period_end=PERIOD_END,
        provider_state_version=provider_state_version,
        last_provider_event_at=None,
        canceled_at=None,
    )


def _event(
    *,
    provider_state_version: int = 5,
    canceled_at: datetime = PERIOD_END,
    status: BillingWebhookEventStatus = (BillingWebhookEventStatus.RECEIVED),
    processing_attempt_count: int = 0,
    processed_at: datetime | None = None,
) -> BillingWebhookEvent:
    provider_event_id = "evt_canceled_01"

    return BillingWebhookEvent(
        id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_CANCELED.value),
        provider_subscription_id="fake_sub_01",
        provider_created_at=canceled_at,
        provider_state_version=provider_state_version,
        payload={
            "id": provider_event_id,
            "type": "subscription.canceled",
            "created_at": canceled_at.isoformat(),
            "data": {
                "provider_subscription_id": "fake_sub_01",
                "provider_state_version": (provider_state_version),
                "price_code": "starter_monthly",
                "status": "canceled",
                "current_period_start": (PERIOD_START.isoformat()),
                "current_period_end": (PERIOD_END.isoformat()),
                "canceled_at": canceled_at.isoformat(),
            },
        },
        payload_sha256="c" * 64,
        signature_timestamp=int(canceled_at.timestamp()),
        status=status,
        processing_attempt_count=(processing_attempt_count),
        processed_at=processed_at,
    )


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.worker_system(
        correlation_id="billing-webhook-cancellation-correlation",
        request_id="billing-webhook-cancellation-request",
    )


def _command(*, webhook_event_id: UUID) -> ProcessBillingWebhookEventCommand:
    return ProcessBillingWebhookEventCommand(
        webhook_event_id=webhook_event_id,
        audit_context=_audit_context(),
    )


def _service(
    *,
    event_repository: RecordingWebhookRepository,
    subscription_repository: (RecordingSubscriptionRepository),
    audit_recorder: RecordingAuditRecorder | None = None,
) -> tuple[ProcessBillingWebhookEventService, RecordingAuditRecorder]:
    resolved_audit_recorder = audit_recorder or RecordingAuditRecorder()
    return (
        ProcessBillingWebhookEventService(
            webhook_event_repository=cast(
                BillingWebhookEventRepository,
                event_repository,
            ),
            subscription_repository=cast(
                SubscriptionRepository,
                subscription_repository,
            ),
            clock=FixedClock(),
            audit_recorder=resolved_audit_recorder,
        ),
        resolved_audit_recorder,
    )


def test_processing_finalizes_scheduled_cancellation() -> None:
    event = _event()
    subscription = _subscription()
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(subscription)
    session = RecordingSession()
    service, recorder = _service(
        event_repository=event_repository,
        subscription_repository=(subscription_repository),
    )

    result = service.execute(
        cast(Session, session),
        _command(webhook_event_id=event.id),
    )

    assert session.commit_count == 2
    assert event_repository.lock_count == 2
    assert event_repository.flush_count == 2
    assert subscription_repository.flush_count == 1

    assert event.status is BillingWebhookEventStatus.PROCESSED
    assert event.processing_attempt_count == 1
    assert event.processed_at == PROCESSED_AT
    assert event.failure_code is None
    assert event.failure_message is None

    assert subscription.status is SubscriptionStatus.CANCELED
    assert subscription.canceled_at == PERIOD_END
    assert subscription.cancel_at_period_end is False
    assert subscription.cancellation_requested_at == (REQUESTED_AT)
    assert subscription.pending_price_code is None
    assert subscription.provider_state_version == 5
    assert subscription.last_provider_event_at == (PERIOD_END)

    assert result.event_type is BillingWebhookEventType.SUBSCRIPTION_CANCELED
    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert result.status is BillingWebhookEventStatus.PROCESSED
    assert result.subscription_id == subscription.id

    assert len(recorder.commands) == 1
    assert recorder.sessions == [cast(Session, session)]
    assert recorder.commands[0].action == (AuditAction.BILLING_WEBHOOK_PROCESSED.value)
    assert recorder.commands[0].resource_type == (AuditResourceType.BILLING_WEBHOOK_EVENT.value)
    assert recorder.commands[0].resource_id == str(event.id)
    assert recorder.commands[0].source is AuditSource.WORKER
    assert recorder.commands[0].metadata == {
        "event_type": BillingWebhookEventType.SUBSCRIPTION_CANCELED.value,
        "processing_outcome": "processed",
    }
    assert recorder.commands[0].idempotency_key == (f"billing-webhook-audit:{event.id}:processed")


def test_stale_cancellation_is_marked_ignored() -> None:
    event = _event(provider_state_version=4)
    subscription = _subscription(provider_state_version=5)
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(subscription)
    service, recorder = _service(
        event_repository=event_repository,
        subscription_repository=(subscription_repository),
    )

    result = service.execute(
        cast(Session, RecordingSession()),
        _command(webhook_event_id=event.id),
    )

    assert result.outcome is BillingWebhookProcessingOutcome.IGNORED
    assert event.status is BillingWebhookEventStatus.IGNORED
    assert event.processed_at == PROCESSED_AT
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.cancel_at_period_end is True
    assert subscription.canceled_at is None
    assert subscription.provider_state_version == 5

    assert len(recorder.commands) == 1
    assert recorder.commands[0].action == (AuditAction.BILLING_WEBHOOK_IGNORED.value)
    assert recorder.commands[0].idempotency_key == (f"billing-webhook-audit:{event.id}:ignored")
    assert recorder.commands[0].metadata == {
        "event_type": BillingWebhookEventType.SUBSCRIPTION_CANCELED.value,
        "processing_outcome": "ignored",
    }


def test_invalid_cancellation_is_persisted_as_terminal() -> None:
    event = _event(
        canceled_at=datetime(
            2026,
            8,
            1,
            12,
            tzinfo=UTC,
        )
    )
    subscription = _subscription(cancel_at_period_end=True)
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(subscription)
    session = RecordingSession()
    service, recorder = _service(
        event_repository=event_repository,
        subscription_repository=(subscription_repository),
    )

    with pytest.raises(BillingWebhookEventTerminalFailureError) as exception_info:
        service.execute(
            cast(Session, session),
            _command(webhook_event_id=event.id),
        )

    assert session.commit_count == 2
    assert exception_info.value.failure_code == "cancellation_boundary_mismatch"
    assert event.status is BillingWebhookEventStatus.FAILED_TERMINAL
    assert event.processed_at == PROCESSED_AT
    assert event.failure_code == ("cancellation_boundary_mismatch")
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.cancel_at_period_end is True
    assert subscription.canceled_at is None
    assert recorder.commands == []


def test_completed_cancellation_replays_without_new_attempt() -> None:
    event = _event(
        status=BillingWebhookEventStatus.PROCESSED,
        processing_attempt_count=2,
        processed_at=PROCESSED_AT,
    )
    subscription = _subscription(
        provider_state_version=5,
        cancel_at_period_end=False,
    )
    subscription.status = SubscriptionStatus.CANCELED
    subscription.canceled_at = PERIOD_END
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(subscription)
    session = RecordingSession()
    service, recorder = _service(
        event_repository=event_repository,
        subscription_repository=(subscription_repository),
    )

    result = service.execute(
        cast(Session, session),
        _command(webhook_event_id=event.id),
    )

    assert session.commit_count == 2
    assert event.processing_attempt_count == 2
    assert event_repository.flush_count == 0
    assert subscription_repository.flush_count == 0
    assert result.event_type is BillingWebhookEventType.SUBSCRIPTION_CANCELED
    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert len(recorder.commands) == 1
    assert recorder.commands[0].action == (AuditAction.BILLING_WEBHOOK_PROCESSED.value)
    assert recorder.commands[0].idempotency_key == (f"billing-webhook-audit:{event.id}:processed")
