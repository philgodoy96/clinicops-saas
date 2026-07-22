import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import cast
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.billing.enums import BillingProvider
from clinicops.billing.exceptions import (
    BillingWebhookEventConflictError,
)
from clinicops.billing.models import BillingWebhookEvent
from clinicops.billing.webhooks.ingest import (
    IngestBillingWebhookCommand,
    IngestBillingWebhookService,
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
    price_code: str,
) -> bytes:
    return json.dumps(
        {
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
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _deliver(
    *,
    service: IngestBillingWebhookService,
    barrier: Barrier,
    raw_body: bytes,
) -> str:
    with Session(get_engine()) as session:
        barrier.wait(timeout=10)

        try:
            result = service.execute(
                session,
                IngestBillingWebhookCommand(
                    provider=BillingProvider.FAKE,
                    raw_body=raw_body,
                    signature_timestamp=(SIGNATURE_TIMESTAMP),
                    correlation_id=str(uuid4()),
                ),
            )
        except BillingWebhookEventConflictError:
            return "conflict"

        session.commit()

        return "duplicate" if result.duplicate else "created"


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


def _load_payload_data(
    provider_event_id: str,
) -> dict[str, object]:
    with Session(get_engine()) as session:
        event = session.scalar(
            select(BillingWebhookEvent).where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id == provider_event_id,
            )
        )

        assert event is not None
        return cast(
            dict[str, object],
            event.payload["data"],
        )


def _cleanup_event(
    provider_event_id: str,
) -> None:
    with Session(get_engine()) as session:
        session.execute(
            delete(BillingWebhookEvent).where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id == provider_event_id,
            )
        )
        session.commit()


def test_concurrent_identical_deliveries_create_one_event() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    raw_body = _raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=(f"fake_sub_{uuid4().hex}"),
        price_code="professional_monthly",
    )
    service = IngestBillingWebhookService()
    barrier = Barrier(2)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _deliver,
                    service=service,
                    barrier=barrier,
                    raw_body=raw_body,
                )
                for _ in range(2)
            ]
            outcomes = [future.result(timeout=20) for future in futures]

        assert sorted(outcomes) == [
            "created",
            "duplicate",
        ]
        assert _event_count(provider_event_id) == 1
    finally:
        _cleanup_event(provider_event_id)


def test_concurrent_conflicting_deliveries_preserve_one_payload() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    provider_subscription_id = f"fake_sub_{uuid4().hex}"
    bodies = (
        _raw_body(
            provider_event_id=provider_event_id,
            provider_subscription_id=(provider_subscription_id),
            price_code="professional_monthly",
        ),
        _raw_body(
            provider_event_id=provider_event_id,
            provider_subscription_id=(provider_subscription_id),
            price_code="starter_yearly",
        ),
    )
    service = IngestBillingWebhookService()
    barrier = Barrier(2)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _deliver,
                    service=service,
                    barrier=barrier,
                    raw_body=raw_body,
                )
                for raw_body in bodies
            ]
            outcomes = [future.result(timeout=20) for future in futures]

        assert sorted(outcomes) == [
            "conflict",
            "created",
        ]
        assert _event_count(provider_event_id) == 1

        payload_data = _load_payload_data(provider_event_id)
        assert payload_data["price_code"] in {
            "professional_monthly",
            "starter_yearly",
        }
    finally:
        _cleanup_event(provider_event_id)
