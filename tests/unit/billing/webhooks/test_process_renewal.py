from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

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

CURRENT_PERIOD_START = datetime(
    2026,
    7,
    22,
    12,
    tzinfo=UTC,
)
CURRENT_PERIOD_END = datetime(
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


class FixedClock:
    def now(self) -> datetime:
        return PROCESSED_AT


class RecordingSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


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
        subscription: Subscription | None,
    ) -> None:
        self.subscription = subscription
        self.requested_provider: BillingProvider | None = None
        self.requested_provider_subscription_id: str | None = None
        self.flush_count = 0

    def get_by_provider_subscription_id_for_update(
        self,
        session: Session,
        *,
        provider: BillingProvider,
        provider_subscription_id: str,
    ) -> Subscription | None:
        self.requested_provider = provider
        self.requested_provider_subscription_id = provider_subscription_id
        return self.subscription

    def flush(self, session: Session) -> None:
        self.flush_count += 1


def _subscription(
    *,
    provider_state_version: int = 3,
    pending_price_code: str | None = ("professional_monthly"),
    cancel_at_period_end: bool = False,
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
        pending_price_code=pending_price_code,
        status=SubscriptionStatus.ACTIVE,
        cancel_at_period_end=cancel_at_period_end,
        cancellation_requested_at=(CURRENT_PERIOD_START if cancel_at_period_end else None),
        current_period_start=CURRENT_PERIOD_START,
        current_period_end=CURRENT_PERIOD_END,
        provider_state_version=provider_state_version,
        last_provider_event_at=None,
        canceled_at=None,
    )


def _event(
    *,
    provider_state_version: int = 4,
    price_code: str = "professional_monthly",
    status: BillingWebhookEventStatus = (BillingWebhookEventStatus.RECEIVED),
    processing_attempt_count: int = 0,
    processed_at: datetime | None = None,
) -> BillingWebhookEvent:
    provider_event_id = "evt_renewed_01"

    return BillingWebhookEvent(
        id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
        provider_subscription_id="fake_sub_01",
        provider_created_at=CURRENT_PERIOD_END,
        provider_state_version=provider_state_version,
        payload={
            "id": provider_event_id,
            "type": "subscription.renewed",
            "created_at": (CURRENT_PERIOD_END.isoformat()),
            "data": {
                "provider_subscription_id": "fake_sub_01",
                "provider_state_version": (provider_state_version),
                "price_code": price_code,
                "status": "active",
                "current_period_start": (CURRENT_PERIOD_END.isoformat()),
                "current_period_end": (NEXT_PERIOD_END.isoformat()),
                "canceled_at": None,
            },
        },
        payload_sha256="a" * 64,
        signature_timestamp=int(CURRENT_PERIOD_END.timestamp()),
        status=status,
        processing_attempt_count=(processing_attempt_count),
        processed_at=processed_at,
    )


def _service(
    *,
    event_repository: RecordingWebhookRepository,
    subscription_repository: (RecordingSubscriptionRepository),
) -> ProcessBillingWebhookEventService:
    return ProcessBillingWebhookEventService(
        webhook_event_repository=cast(
            BillingWebhookEventRepository,
            event_repository,
        ),
        subscription_repository=cast(
            SubscriptionRepository,
            subscription_repository,
        ),
        clock=FixedClock(),
    )


def test_processing_claim_and_renewal_use_two_commits() -> None:
    event = _event()
    subscription = _subscription()
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(subscription)
    session = RecordingSession()

    result = _service(
        event_repository=event_repository,
        subscription_repository=(subscription_repository),
    ).execute(
        cast(Session, session),
        ProcessBillingWebhookEventCommand(webhook_event_id=event.id),
    )

    assert session.commit_count == 2
    assert event_repository.lock_count == 2
    assert event_repository.flush_count == 2
    assert subscription_repository.flush_count == 1
    assert subscription_repository.requested_provider is BillingProvider.FAKE
    assert subscription_repository.requested_provider_subscription_id == "fake_sub_01"

    assert event.status is BillingWebhookEventStatus.PROCESSED
    assert event.processing_attempt_count == 1
    assert event.processed_at == PROCESSED_AT
    assert event.failure_code is None
    assert event.failure_message is None

    assert subscription.price_code == ("professional_monthly")
    assert subscription.plan is BillingPlan.PROFESSIONAL
    assert subscription.unit_amount == 9900
    assert subscription.pending_price_code is None
    assert subscription.provider_state_version == 4

    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert result.subscription_id == subscription.id
    assert result.status is BillingWebhookEventStatus.PROCESSED


def test_stale_event_is_ignored_without_subscription_mutation() -> None:
    event = _event(provider_state_version=3)
    subscription = _subscription(provider_state_version=4)
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(subscription)

    result = _service(
        event_repository=event_repository,
        subscription_repository=(subscription_repository),
    ).execute(
        cast(Session, RecordingSession()),
        ProcessBillingWebhookEventCommand(webhook_event_id=event.id),
    )

    assert result.outcome is BillingWebhookProcessingOutcome.IGNORED
    assert event.status is BillingWebhookEventStatus.IGNORED
    assert event.processed_at == PROCESSED_AT
    assert subscription.price_code == "starter_monthly"
    assert subscription.pending_price_code == ("professional_monthly")
    assert subscription.provider_state_version == 4


def test_missing_subscription_is_persisted_as_retryable() -> None:
    event = _event()
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(None)
    session = RecordingSession()

    with pytest.raises(BillingWebhookEventRetryableFailureError) as exception_info:
        _service(
            event_repository=event_repository,
            subscription_repository=(subscription_repository),
        ).execute(
            cast(Session, session),
            ProcessBillingWebhookEventCommand(webhook_event_id=event.id),
        )

    assert session.commit_count == 2
    assert exception_info.value.failure_code == "billing_webhook_subscription_not_found"
    assert event.status is BillingWebhookEventStatus.FAILED_RETRYABLE
    assert event.processed_at is None
    assert event.failure_code == ("billing_webhook_subscription_not_found")


def test_invalid_renewal_is_persisted_as_terminal() -> None:
    event = _event()
    subscription = _subscription(cancel_at_period_end=True)
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(subscription)
    session = RecordingSession()

    with pytest.raises(BillingWebhookEventTerminalFailureError) as exception_info:
        _service(
            event_repository=event_repository,
            subscription_repository=(subscription_repository),
        ).execute(
            cast(Session, session),
            ProcessBillingWebhookEventCommand(webhook_event_id=event.id),
        )

    assert session.commit_count == 2
    assert exception_info.value.failure_code == "cancellation_pending"
    assert event.status is BillingWebhookEventStatus.FAILED_TERMINAL
    assert event.processed_at == PROCESSED_AT
    assert event.failure_code == "cancellation_pending"
    assert subscription.price_code == "starter_monthly"
    assert subscription.pending_price_code == ("professional_monthly")


def test_completed_event_replays_without_attempt_increment() -> None:
    event = _event(
        status=BillingWebhookEventStatus.PROCESSED,
        processing_attempt_count=2,
        processed_at=PROCESSED_AT,
    )
    subscription = _subscription(
        provider_state_version=4,
        pending_price_code=None,
    )
    event_repository = RecordingWebhookRepository(event)
    subscription_repository = RecordingSubscriptionRepository(subscription)
    session = RecordingSession()

    result = _service(
        event_repository=event_repository,
        subscription_repository=(subscription_repository),
    ).execute(
        cast(Session, session),
        ProcessBillingWebhookEventCommand(webhook_event_id=event.id),
    )

    assert session.commit_count == 2
    assert event.processing_attempt_count == 2
    assert event_repository.flush_count == 0
    assert subscription_repository.flush_count == 0
    assert result.outcome is BillingWebhookProcessingOutcome.APPLIED
    assert result.status is BillingWebhookEventStatus.PROCESSED
