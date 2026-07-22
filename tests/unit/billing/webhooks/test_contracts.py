from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from clinicops.billing.enums import (
    BillingWebhookEventType,
    SubscriptionStatus,
)
from clinicops.billing.webhooks.contracts import (
    BillingWebhookEventEnvelope,
)

RENEWED_EVENT_DATA: dict[str, object] = {
    "provider_subscription_id": "fake_sub_01",
    "provider_state_version": 4,
    "price_code": "professional_monthly",
    "status": "active",
    "current_period_start": "2026-08-22T12:00:00Z",
    "current_period_end": "2026-09-22T12:00:00Z",
    "canceled_at": None,
}

RENEWED_EVENT: dict[str, object] = {
    "id": "evt_renewed_01",
    "type": "subscription.renewed",
    "created_at": "2026-08-22T12:00:00Z",
    "data": RENEWED_EVENT_DATA,
}


def test_renewed_event_parses_canonical_provider_state() -> None:
    event = BillingWebhookEventEnvelope.model_validate(RENEWED_EVENT)

    assert event.provider_event_id == "evt_renewed_01"
    assert event.event_type is BillingWebhookEventType.SUBSCRIPTION_RENEWED
    assert event.created_at == datetime(
        2026,
        8,
        22,
        12,
        tzinfo=UTC,
    )
    assert event.data.provider_subscription_id == "fake_sub_01"
    assert event.data.provider_state_version == 4
    assert event.data.status is SubscriptionStatus.ACTIVE
    assert event.data.canceled_at is None


def test_canceled_event_requires_final_cancellation_state() -> None:
    event = BillingWebhookEventEnvelope.model_validate(
        {
            "id": "evt_canceled_01",
            "type": "subscription.canceled",
            "created_at": "2026-08-22T12:00:00Z",
            "data": {
                "provider_subscription_id": ("fake_sub_01"),
                "provider_state_version": 5,
                "price_code": "starter_monthly",
                "status": "canceled",
                "current_period_start": ("2026-07-22T12:00:00Z"),
                "current_period_end": ("2026-08-22T12:00:00Z"),
                "canceled_at": ("2026-08-22T12:00:00Z"),
            },
        }
    )

    assert event.event_type is BillingWebhookEventType.SUBSCRIPTION_CANCELED
    assert event.data.status is SubscriptionStatus.CANCELED
    assert event.data.canceled_at == datetime(
        2026,
        8,
        22,
        12,
        tzinfo=UTC,
    )


def test_contract_serializes_provider_field_names() -> None:
    event = BillingWebhookEventEnvelope.model_validate(RENEWED_EVENT)

    serialized = event.model_dump(
        mode="json",
        by_alias=True,
    )

    assert serialized["id"] == "evt_renewed_01"
    assert serialized["type"] == "subscription.renewed"
    assert "provider_event_id" not in serialized
    assert "event_type" not in serialized


@pytest.mark.parametrize(
    "mutation",
    [
        {
            "type": "subscription.renewed",
            "data": {
                **RENEWED_EVENT_DATA,
                "status": "canceled",
                "canceled_at": "2026-08-22T12:00:00Z",
            },
        },
        {
            "type": "subscription.canceled",
            "data": {
                **RENEWED_EVENT_DATA,
                "status": "canceled",
                "canceled_at": None,
            },
        },
        {
            "data": {
                **RENEWED_EVENT_DATA,
                "provider_state_version": 0,
            },
        },
        {
            "data": {
                **RENEWED_EVENT_DATA,
                "current_period_start": "2026-09-22T12:00:00Z",
                "current_period_end": "2026-08-22T12:00:00Z",
            },
        },
    ],
)
def test_contract_rejects_inconsistent_provider_state(
    mutation: dict[str, object],
) -> None:
    payload = {
        **RENEWED_EVENT,
        **mutation,
    }

    with pytest.raises(ValidationError):
        BillingWebhookEventEnvelope.model_validate(payload)


def test_contract_rejects_naive_event_timestamp() -> None:
    payload = {
        **RENEWED_EVENT,
        "created_at": datetime(
            2026,
            8,
            22,
            12,
        ),
    }

    with pytest.raises(
        ValidationError,
        match="timezone-aware",
    ):
        BillingWebhookEventEnvelope.model_validate(payload)


def test_contract_rejects_unknown_fields() -> None:
    payload = {
        **RENEWED_EVENT,
        "tenant_id": "untrusted-tenant-id",
    }

    with pytest.raises(ValidationError):
        BillingWebhookEventEnvelope.model_validate(payload)
