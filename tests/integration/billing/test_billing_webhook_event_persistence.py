from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingProvider,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
)
from clinicops.billing.models import BillingWebhookEvent
from clinicops.billing.repositories import (
    BillingWebhookEventRepository,
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


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _event(
    *,
    provider_event_id: str | None = None,
    provider_subscription_id: str = "fake_sub_unknown",
    payload_sha256: str = "a" * 64,
    signature_timestamp: int = SIGNATURE_TIMESTAMP,
) -> BillingWebhookEvent:
    resolved_event_id = provider_event_id or f"evt_{uuid4().hex}"
    payload = {
        "id": resolved_event_id,
        "type": (BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
        "created_at": (PROVIDER_CREATED_AT.isoformat()),
        "data": {
            "provider_subscription_id": (provider_subscription_id),
            "provider_state_version": 4,
            "price_code": "professional_monthly",
            "status": "active",
            "current_period_start": ("2026-08-22T12:00:00+00:00"),
            "current_period_end": ("2026-09-22T12:00:00+00:00"),
            "canceled_at": None,
        },
    }

    return BillingWebhookEvent(
        provider=BillingProvider.FAKE,
        provider_event_id=resolved_event_id,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
        provider_subscription_id=(provider_subscription_id),
        provider_created_at=PROVIDER_CREATED_AT,
        provider_state_version=4,
        payload=payload,
        payload_sha256=payload_sha256,
        signature_timestamp=signature_timestamp,
        correlation_id=str(uuid4()),
    )


def test_repository_persists_authenticated_event_metadata(
    db_session: Session,
) -> None:
    repository = BillingWebhookEventRepository()
    event = repository.add_and_flush(
        db_session,
        _event(),
    )

    loaded = repository.get_by_provider_event_id(
        db_session,
        provider=BillingProvider.FAKE,
        provider_event_id=event.provider_event_id,
    )

    assert loaded is event
    assert event.status is BillingWebhookEventStatus.RECEIVED
    assert event.processing_attempt_count == 0
    assert event.provider_subscription_id == "fake_sub_unknown"
    assert event.provider_created_at == (PROVIDER_CREATED_AT)
    assert event.provider_state_version == 4
    assert event.payload_sha256 == "a" * 64
    assert event.signature_timestamp == SIGNATURE_TIMESTAMP
    assert event.processed_at is None
    assert event.failure_code is None
    assert event.failure_message is None
    assert event.created_at is not None
    assert event.updated_at is not None


def test_unknown_local_subscription_identity_is_accepted(
    db_session: Session,
) -> None:
    repository = BillingWebhookEventRepository()
    provider_subscription_id = f"fake_sub_{uuid4().hex}"

    event = repository.add_and_flush(
        db_session,
        _event(provider_subscription_id=(provider_subscription_id)),
    )

    assert event.provider_subscription_id == provider_subscription_id


def test_processing_state_requires_claim_attempt(
    db_session: Session,
) -> None:
    repository = BillingWebhookEventRepository()
    event = repository.add_and_flush(
        db_session,
        _event(),
    )

    event.status = BillingWebhookEventStatus.PROCESSING

    with pytest.raises(IntegrityError):
        repository.flush(db_session)


def test_processing_state_accepts_positive_attempt_count(
    db_session: Session,
) -> None:
    repository = BillingWebhookEventRepository()
    event = repository.add_and_flush(
        db_session,
        _event(),
    )

    event.status = BillingWebhookEventStatus.PROCESSING
    event.processing_attempt_count = 1
    repository.flush(db_session)

    assert event.status is BillingWebhookEventStatus.PROCESSING
    assert event.processing_attempt_count == 1


@pytest.mark.parametrize(
    ("payload_sha256", "signature_timestamp"),
    [
        ("not-a-sha256", SIGNATURE_TIMESTAMP),
        ("a" * 64, 0),
    ],
)
def test_database_rejects_invalid_authentication_metadata(
    db_session: Session,
    payload_sha256: str,
    signature_timestamp: int,
) -> None:
    repository = BillingWebhookEventRepository()

    with pytest.raises(IntegrityError):
        repository.add_and_flush(
            db_session,
            _event(
                payload_sha256=payload_sha256,
                signature_timestamp=(signature_timestamp),
            ),
        )
