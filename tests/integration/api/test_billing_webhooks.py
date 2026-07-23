import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.api.v1.billing.webhooks import (
    get_billing_webhook_clock,
)
from clinicops.billing.enums import (
    BillingProvider,
    BillingWebhookEventStatus,
)
from clinicops.billing.models import (
    BillingWebhookEvent,
    Subscription,
)
from clinicops.billing.webhooks.signatures import (
    sign_billing_webhook_payload,
)
from clinicops.core.config import Environment, Settings
from clinicops.db.session import get_engine
from clinicops.jobs.models import BackgroundJob
from clinicops.main import create_app
from tests.conftest import IsolatedSettings

SECRET = "integration-billing-webhook-secret-123456"
_BILLING_WEBHOOK_PROCESS_IDEMPOTENCY_PREFIX = "billing-webhook-process"
NOW = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
TIMESTAMP = int(NOW.timestamp())


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _raw_body(
    *,
    provider_event_id: str,
    provider_subscription_id: str,
    price_code: str = "professional_monthly",
) -> bytes:
    return json.dumps(
        {
            "id": provider_event_id,
            "type": "subscription.renewed",
            "created_at": NOW.isoformat(),
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


def _settings() -> Settings:
    return IsolatedSettings(
        environment=Environment.TEST,
        billing_webhook_secret=SecretStr(SECRET),
    )


@contextmanager
def _client() -> Iterator[TestClient]:
    application = create_app(_settings())
    application.dependency_overrides[get_billing_webhook_clock] = lambda: FixedClock()

    try:
        with TestClient(application) as client:
            yield client
    finally:
        application.dependency_overrides.clear()


def _signature(raw_body: bytes) -> str:
    return sign_billing_webhook_payload(
        raw_body=raw_body,
        secret=SECRET,
        timestamp=TIMESTAMP,
    )


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
        event_ids = list(
            session.scalars(
                select(BillingWebhookEvent.id).where(
                    BillingWebhookEvent.provider == BillingProvider.FAKE,
                    BillingWebhookEvent.provider_event_id.in_(provider_event_ids),
                )
            )
        )

        if event_ids:
            idempotency_keys = [
                f"{_BILLING_WEBHOOK_PROCESS_IDEMPOTENCY_PREFIX}:{event_id}"
                for event_id in event_ids
            ]
            session.execute(
                delete(BackgroundJob).where(BackgroundJob.idempotency_key.in_(idempotency_keys))
            )

        session.execute(
            delete(BillingWebhookEvent).where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id.in_(provider_event_ids),
            )
        )
        session.commit()


def test_signed_webhook_is_durably_acknowledged_without_bearer_auth() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    provider_subscription_id = f"fake_sub_unknown_{uuid4().hex}"
    correlation_id = str(uuid4())
    raw_body = _raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=(provider_subscription_id),
    )

    try:
        with _client() as client:
            response = client.post(
                "/api/v1/billing/webhooks/fake",
                content=raw_body,
                headers={
                    "X-Billing-Signature": (_signature(raw_body)),
                    "X-Correlation-ID": correlation_id,
                },
            )

        assert response.status_code == 202
        assert response.json() == {"received": True}
        assert response.headers["X-Correlation-ID"] == correlation_id

        event = _load_event(provider_event_id)

        assert event.status is BillingWebhookEventStatus.RECEIVED
        assert event.provider_subscription_id == provider_subscription_id
        assert event.correlation_id == correlation_id
        assert event.processing_attempt_count == 0

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


def test_http_duplicate_delivery_returns_same_public_receipt() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    raw_body = _raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=(f"fake_sub_{uuid4().hex}"),
    )
    headers = {
        "X-Billing-Signature": _signature(raw_body),
    }

    try:
        with _client() as client:
            first = client.post(
                "/api/v1/billing/webhooks/fake",
                content=raw_body,
                headers=headers,
            )
            duplicate = client.post(
                "/api/v1/billing/webhooks/fake",
                content=raw_body,
                headers=headers,
            )

        assert first.status_code == 202
        assert duplicate.status_code == 202
        assert first.json() == {"received": True}
        assert duplicate.json() == {"received": True}
        assert _event_count(provider_event_id) == 1
    finally:
        _cleanup_events(provider_event_id)


def test_http_conflicting_duplicate_returns_problem_details() -> None:
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

    try:
        with _client() as client:
            first = client.post(
                "/api/v1/billing/webhooks/fake",
                content=first_body,
                headers={
                    "X-Billing-Signature": (_signature(first_body)),
                },
            )
            conflict = client.post(
                "/api/v1/billing/webhooks/fake",
                content=conflicting_body,
                headers={
                    "X-Billing-Signature": (_signature(conflicting_body)),
                },
            )

        assert first.status_code == 202
        assert conflict.status_code == 409
        assert conflict.headers["content-type"].startswith("application/problem+json")
        assert conflict.json()["code"] == ("billing_webhook_event_conflict")
        assert _event_count(provider_event_id) == 1

        event = _load_event(provider_event_id)
        payload_data = cast(
            dict[str, object],
            event.payload["data"],
        )
        assert payload_data["price_code"] == "professional_monthly"
    finally:
        _cleanup_events(provider_event_id)


def test_invalid_signature_is_rejected_before_json_parsing() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    malformed_body = b'{"id":"' + provider_event_id.encode("utf-8") + b'","type":'

    try:
        with _client() as client:
            response = client.post(
                "/api/v1/billing/webhooks/fake",
                content=malformed_body,
                headers={
                    "X-Billing-Signature": (f"t={TIMESTAMP},v1=" + ("a" * 64)),
                },
            )

        assert response.status_code == 401
        assert response.json()["code"] == ("billing_webhook_authentication_failed")
        assert _event_count(provider_event_id) == 0
    finally:
        _cleanup_events(provider_event_id)


def test_valid_signature_with_malformed_json_is_not_persisted() -> None:
    provider_event_id = f"evt_{uuid4().hex}"
    malformed_body = b'{"id":"' + provider_event_id.encode("utf-8") + b'","type":'

    try:
        with _client() as client:
            response = client.post(
                "/api/v1/billing/webhooks/fake",
                content=malformed_body,
                headers={
                    "X-Billing-Signature": (_signature(malformed_body)),
                },
            )

        assert response.status_code == 400
        assert response.json()["code"] == ("billing_webhook_payload_invalid")
        assert _event_count(provider_event_id) == 0
    finally:
        _cleanup_events(provider_event_id)
