import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingProvider,
    BillingWebhookEventStatus,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventConflictError,
)
from clinicops.billing.models import (
    BillingWebhookEvent,
    Subscription,
)
from clinicops.billing.webhooks.ingest import (
    IngestBillingWebhookCommand,
    IngestBillingWebhookService,
    IngestedBillingWebhook,
)
from clinicops.db.session import get_engine

PROVIDER_CREATED_AT = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
SIGNATURE_TIMESTAMP = int(PROVIDER_CREATED_AT.timestamp())


def _raw_body(
    *,
    provider_event_id: str,
    provider_subscription_id: str,
    price_code: str = "professional_monthly",
) -> bytes:
    payload = {
        "id": provider_event_id,
        "type": "subscription.renewed",
        "created_at": (PROVIDER_CREATED_AT.isoformat()),
        "data": {
            "provider_subscription_id": (provider_subscription_id),
            "provider_state_version": 4,
            "price_code": price_code,
            "status": "active",
            "current_period_start": ("2026-08-22T12:00:00+00:00"),
            "current_period_end": ("2026-09-22T12:00:00+00:00"),
            "canceled_at": None,
        },
    }

    return json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _execute_and_commit(
    *,
    service: IngestBillingWebhookService,
    raw_body: bytes,
    correlation_id: str | None = None,
) -> IngestedBillingWebhook:
    with Session(get_engine()) as session:
        result = service.execute(
            session,
            IngestBillingWebhookCommand(
                provider=BillingProvider.FAKE,
                raw_body=raw_body,
                signature_timestamp=(SIGNATURE_TIMESTAMP),
                correlation_id=correlation_id,
            ),
        )
        session.commit()
        return result


def _load_event(
    provider_event_id: str,
) -> BillingWebhookEvent:
    with Session(get_engine()) as session:
        event = session.scalar(
            select(BillingWebhookEvent).where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id == provider_event_id,
            )
        )

        assert event is not None
        return event


def _event_count(
    provider_event_id: str,
) -> int:
    with Session(get_engine()) as session:
        count = session.scalar(
            select(func.count())
            .select_from(BillingWebhookEvent)
            .where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id == provider_event_id,
            )
        )

        return count or 0


def _cleanup_events(
    *provider_event_ids: str,
) -> None:
    with Session(get_engine()) as session:
        session.execute(
            delete(BillingWebhookEvent).where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id.in_(provider_event_ids),
            )
        )
        session.commit()


def test_ingestion_persists_authenticated_event_without_local_subscription() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    provider_subscription_id = f"fake_sub_unknown_{uuid4().hex}"
    correlation_id = str(uuid4())
    raw_body = _raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=(provider_subscription_id),
    )
    service = IngestBillingWebhookService()

    try:
        result = _execute_and_commit(
            service=service,
            raw_body=raw_body,
            correlation_id=correlation_id,
        )
        event = _load_event(provider_event_id)

        assert result.webhook_event_id == event.id
        assert result.provider is BillingProvider.FAKE
        assert result.provider_event_id == (provider_event_id)
        assert result.status is BillingWebhookEventStatus.RECEIVED
        assert result.duplicate is False

        assert event.provider_subscription_id == provider_subscription_id
        assert event.provider_created_at == (PROVIDER_CREATED_AT)
        assert event.provider_state_version == 4
        assert event.payload_sha256 == sha256(raw_body).hexdigest()
        assert event.signature_timestamp == SIGNATURE_TIMESTAMP
        assert event.correlation_id == correlation_id
        assert event.status is BillingWebhookEventStatus.RECEIVED
        assert event.processing_attempt_count == 0
        assert event.processed_at is None
        assert event.failure_code is None
        assert event.failure_message is None

        payload_data = cast(
            dict[str, object],
            event.payload["data"],
        )
        assert payload_data["provider_subscription_id"] == provider_subscription_id
        assert payload_data["price_code"] == "professional_monthly"

        with Session(get_engine()) as session:
            local_subscription = session.scalar(
                select(Subscription).where(
                    Subscription.provider == BillingProvider.FAKE,
                    Subscription.provider_subscription_id == provider_subscription_id,
                )
            )

            assert local_subscription is None
    finally:
        _cleanup_events(provider_event_id)


def test_identical_persisted_delivery_replays_one_event() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    raw_body = _raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=(f"fake_sub_{uuid4().hex}"),
    )
    service = IngestBillingWebhookService()

    try:
        first = _execute_and_commit(
            service=service,
            raw_body=raw_body,
        )
        replayed = _execute_and_commit(
            service=service,
            raw_body=raw_body,
        )

        assert first.duplicate is False
        assert replayed.duplicate is True
        assert replayed.webhook_event_id == first.webhook_event_id
        assert _event_count(provider_event_id) == 1
    finally:
        _cleanup_events(provider_event_id)


def test_reused_event_id_with_different_raw_payload_conflicts() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    provider_subscription_id = f"fake_sub_{uuid4().hex}"
    first_body = _raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=(provider_subscription_id),
        price_code="professional_monthly",
    )
    conflicting_body = _raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=(provider_subscription_id),
        price_code="starter_yearly",
    )
    service = IngestBillingWebhookService()

    try:
        _execute_and_commit(
            service=service,
            raw_body=first_body,
        )

        with Session(get_engine()) as session, pytest.raises(BillingWebhookEventConflictError):
            service.execute(
                session,
                IngestBillingWebhookCommand(
                    provider=BillingProvider.FAKE,
                    raw_body=conflicting_body,
                    signature_timestamp=(SIGNATURE_TIMESTAMP),
                ),
            )

        event = _load_event(provider_event_id)
        payload_data = cast(
            dict[str, object],
            event.payload["data"],
        )

        assert _event_count(provider_event_id) == 1
        assert event.payload_sha256 == sha256(first_body).hexdigest()
        assert payload_data["price_code"] == "professional_monthly"
    finally:
        _cleanup_events(provider_event_id)
