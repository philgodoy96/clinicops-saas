from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
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


class FailOnCompletionWebhookEventRepository(BillingWebhookEventRepository):
    """Fail after Transaction B has flushed its dirty state."""

    def __init__(self) -> None:
        self.flush_count = 0

    def flush(self, session: Session) -> None:
        self.flush_count += 1

        if self.flush_count == 2:
            raise RuntimeError("Simulated completion persistence failure.")

        super().flush(session)


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
) -> ProcessedBillingWebhookEvent:
    resolved_service = (
        service if service is not None else ProcessBillingWebhookEventService(clock=FixedClock())
    )

    with Session(get_engine()) as session:
        return resolved_service.execute(
            session,
            ProcessBillingWebhookEventCommand(webhook_event_id=webhook_event_id),
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
    finally:
        _cleanup(
            tenant_id=fixture.tenant_id,
            webhook_event_ids=(event_id,),
        )
