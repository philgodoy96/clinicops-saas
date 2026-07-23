from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from threading import Barrier
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.audit.context import AuditRecordingContext
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
    BillingWebhookEventProcessingConflictError,
)
from clinicops.billing.models import (
    BillingCustomer,
    BillingWebhookEvent,
    Subscription,
)
from clinicops.billing.webhooks.process import (
    ProcessBillingWebhookEventCommand,
    ProcessBillingWebhookEventService,
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


class FixedClock:
    def now(self) -> datetime:
        return PROCESSED_AT


@dataclass(frozen=True, slots=True)
class ConcurrentProcessingFixture:
    tenant_id: UUID
    subscription_id: UUID
    provider_subscription_id: str


def _persist_fixture() -> ConcurrentProcessingFixture:
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()
    provider_subscription_id = f"fake_sub_{uuid4().hex}"

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Concurrent Webhook Clinic {tenant_id.hex}"),
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
                pending_price_code=None,
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=False,
                cancellation_requested_at=None,
                current_period_start=PERIOD_START,
                current_period_end=PERIOD_END,
                provider_state_version=4,
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return ConcurrentProcessingFixture(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        provider_subscription_id=(provider_subscription_id),
    )


def _renewal_event(
    fixture: ConcurrentProcessingFixture,
    *,
    provider_state_version: int = 5,
) -> BillingWebhookEvent:
    event_id = uuid4()
    provider_event_id = f"evt_{uuid4().hex}"

    return BillingWebhookEvent(
        id=event_id,
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
        provider_subscription_id=(fixture.provider_subscription_id),
        provider_created_at=PERIOD_END,
        provider_state_version=(provider_state_version),
        payload={
            "id": provider_event_id,
            "type": "subscription.renewed",
            "created_at": PERIOD_END.isoformat(),
            "data": {
                "provider_subscription_id": (fixture.provider_subscription_id),
                "provider_state_version": (provider_state_version),
                "price_code": "starter_monthly",
                "status": "active",
                "current_period_start": (PERIOD_END.isoformat()),
                "current_period_end": (NEXT_PERIOD_END.isoformat()),
                "canceled_at": None,
            },
        },
        payload_sha256=sha256(provider_event_id.encode("utf-8")).hexdigest(),
        signature_timestamp=int(PERIOD_END.timestamp()),
        status=BillingWebhookEventStatus.RECEIVED,
        processing_attempt_count=0,
    )


def _cancellation_event(
    fixture: ConcurrentProcessingFixture,
    *,
    provider_state_version: int = 6,
) -> BillingWebhookEvent:
    event_id = uuid4()
    provider_event_id = f"evt_{uuid4().hex}"

    return BillingWebhookEvent(
        id=event_id,
        provider=BillingProvider.FAKE,
        provider_event_id=provider_event_id,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_CANCELED.value),
        provider_subscription_id=(fixture.provider_subscription_id),
        provider_created_at=NEXT_PERIOD_END,
        provider_state_version=(provider_state_version),
        payload={
            "id": provider_event_id,
            "type": "subscription.canceled",
            "created_at": NEXT_PERIOD_END.isoformat(),
            "data": {
                "provider_subscription_id": (fixture.provider_subscription_id),
                "provider_state_version": (provider_state_version),
                "price_code": "starter_monthly",
                "status": "canceled",
                "current_period_start": (PERIOD_END.isoformat()),
                "current_period_end": (NEXT_PERIOD_END.isoformat()),
                "canceled_at": (NEXT_PERIOD_END.isoformat()),
            },
        },
        payload_sha256=sha256(provider_event_id.encode("utf-8")).hexdigest(),
        signature_timestamp=int(NEXT_PERIOD_END.timestamp()),
        status=BillingWebhookEventStatus.RECEIVED,
        processing_attempt_count=0,
    )


def _persist_events(
    *events: BillingWebhookEvent,
) -> None:
    with Session(get_engine()) as session:
        session.add_all(events)
        session.commit()


def _process_concurrently(
    *,
    barrier: Barrier,
    webhook_event_id: UUID,
) -> tuple[UUID, str]:
    service = ProcessBillingWebhookEventService(clock=FixedClock())

    barrier.wait(timeout=10)

    with Session(get_engine()) as session:
        try:
            result = service.execute(
                session,
                ProcessBillingWebhookEventCommand(
                    webhook_event_id=webhook_event_id,
                    audit_context=AuditRecordingContext.worker_system(
                        correlation_id=(f"concurrent-webhook-{webhook_event_id}"),
                    ),
                ),
            )
        except BillingWebhookEventProcessingConflictError:
            return (
                webhook_event_id,
                "processing_conflict",
            )

    return (
        webhook_event_id,
        result.outcome.value,
    )


def _load_state(
    fixture: ConcurrentProcessingFixture,
    event_ids: tuple[UUID, ...],
) -> tuple[
    Subscription,
    dict[UUID, BillingWebhookEvent],
]:
    with Session(get_engine()) as session:
        subscription = session.get(
            Subscription,
            fixture.subscription_id,
        )
        events = {
            event.id: event
            for event in session.scalars(
                select(BillingWebhookEvent).where(BillingWebhookEvent.id.in_(event_ids))
            ).all()
        }

        assert subscription is not None
        assert len(events) == len(event_ids)

        return subscription, events


def _cleanup(
    fixture: ConcurrentProcessingFixture,
    *event_ids: UUID,
) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(BillingWebhookEvent).where(BillingWebhookEvent.id.in_(event_ids)))
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == fixture.tenant_id))
        session.execute(delete(Subscription).where(Subscription.tenant_id == fixture.tenant_id))
        session.execute(
            delete(BillingCustomer).where(BillingCustomer.tenant_id == fixture.tenant_id)
        )
        session.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
        session.commit()


def test_concurrent_processing_of_same_event_applies_once() -> None:
    fixture = _persist_fixture()
    event = _renewal_event(fixture)
    event_id = event.id
    _persist_events(event)
    barrier = Barrier(2)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _process_concurrently,
                    barrier=barrier,
                    webhook_event_id=event_id,
                )
                for _ in range(2)
            ]
            outcomes = [future.result(timeout=20)[1] for future in futures]

        subscription, events = _load_state(
            fixture,
            (event_id,),
        )
        persisted_event = events[event_id]

        assert BillingWebhookProcessingOutcome.APPLIED.value in outcomes
        assert set(outcomes).issubset(
            {
                BillingWebhookProcessingOutcome.APPLIED.value,
                "processing_conflict",
            }
        )

        assert persisted_event.status is BillingWebhookEventStatus.PROCESSED
        assert persisted_event.processing_attempt_count == 1
        assert persisted_event.processed_at == PROCESSED_AT

        assert subscription.provider_state_version == 5
        assert subscription.current_period_start == (PERIOD_END)
        assert subscription.current_period_end == (NEXT_PERIOD_END)
        assert subscription.status is SubscriptionStatus.ACTIVE
    finally:
        _cleanup(
            fixture,
            event_id,
        )


def test_concurrent_events_serialize_on_subscription_version() -> None:
    fixture = _persist_fixture()
    renewal = _renewal_event(
        fixture,
        provider_state_version=5,
    )
    cancellation = _cancellation_event(
        fixture,
        provider_state_version=6,
    )
    renewal_id = renewal.id
    cancellation_id = cancellation.id
    _persist_events(
        renewal,
        cancellation,
    )
    barrier = Barrier(2)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _process_concurrently,
                    barrier=barrier,
                    webhook_event_id=event_id,
                )
                for event_id in (
                    renewal_id,
                    cancellation_id,
                )
            ]
            outcomes = dict(future.result(timeout=20) for future in futures)

        subscription, events = _load_state(
            fixture,
            (
                renewal_id,
                cancellation_id,
            ),
        )
        persisted_renewal = events[renewal_id]
        persisted_cancellation = events[cancellation_id]

        assert outcomes[cancellation_id] == (BillingWebhookProcessingOutcome.APPLIED.value)
        assert outcomes[renewal_id] in {
            BillingWebhookProcessingOutcome.APPLIED.value,
            BillingWebhookProcessingOutcome.IGNORED.value,
        }

        assert persisted_cancellation.status is BillingWebhookEventStatus.PROCESSED
        assert persisted_renewal.status in {
            BillingWebhookEventStatus.PROCESSED,
            BillingWebhookEventStatus.IGNORED,
        }
        assert persisted_cancellation.processing_attempt_count == 1
        assert persisted_renewal.processing_attempt_count == 1
        assert persisted_cancellation.failure_code is None
        assert persisted_renewal.failure_code is None

        assert subscription.status is SubscriptionStatus.CANCELED
        assert subscription.canceled_at == NEXT_PERIOD_END
        assert subscription.cancel_at_period_end is False
        assert subscription.provider_state_version == 6
        assert subscription.current_period_start == (PERIOD_END)
        assert subscription.current_period_end == (NEXT_PERIOD_END)
        assert subscription.last_provider_event_at == (NEXT_PERIOD_END)
    finally:
        _cleanup(
            fixture,
            renewal_id,
            cancellation_id,
        )
