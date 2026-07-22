from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingProvider,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventAlreadyExistsError,
    BillingWebhookEventConflictError,
    BillingWebhookPayloadInvalidError,
)
from clinicops.billing.models import BillingWebhookEvent
from clinicops.billing.repositories import (
    BillingWebhookEventRepository,
)
from clinicops.billing.webhooks.ingest import (
    IngestBillingWebhookCommand,
    IngestBillingWebhookService,
)

PROVIDER_EVENT_ID = "evt_renewed_01"
PROVIDER_SUBSCRIPTION_ID = "fake_sub_unknown"
PROVIDER_CREATED_AT = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
SIGNATURE_TIMESTAMP = int(PROVIDER_CREATED_AT.timestamp())
CORRELATION_ID = "1a414d78-3ddb-4188-93c8-aa2f20f45e75"
RAW_BODY = (
    b'{"id":"evt_renewed_01",'
    b'"type":"subscription.renewed",'
    b'"created_at":"2026-08-22T12:00:00Z",'
    b'"data":{'
    b'"provider_subscription_id":"fake_sub_unknown",'
    b'"provider_state_version":4,'
    b'"price_code":"professional_monthly",'
    b'"status":"active",'
    b'"current_period_start":"2026-08-22T12:00:00Z",'
    b'"current_period_end":"2026-09-22T12:00:00Z",'
    b'"canceled_at":null}}'
)


class FakeSession:
    def __init__(self) -> None:
        self.rollback_count = 0

    def rollback(self) -> None:
        self.rollback_count += 1


class InMemoryWebhookEventRepository:
    def __init__(
        self,
        *,
        existing: BillingWebhookEvent | None = None,
        concurrent_winner: (BillingWebhookEvent | None) = None,
        raise_unique_conflict: bool = False,
    ) -> None:
        self.existing = existing
        self.concurrent_winner = concurrent_winner
        self.raise_unique_conflict = raise_unique_conflict
        self.added: BillingWebhookEvent | None = None
        self.lookup_count = 0

    def get_by_provider_event_id(
        self,
        session: Session,
        *,
        provider: BillingProvider,
        provider_event_id: str,
    ) -> BillingWebhookEvent | None:
        self.lookup_count += 1

        if self.lookup_count == 1:
            candidate = self.existing
        else:
            candidate = (
                self.concurrent_winner if self.concurrent_winner is not None else self.existing
            )

        if (
            candidate is not None
            and candidate.provider is provider
            and candidate.provider_event_id == provider_event_id
        ):
            return candidate

        return None

    def add_and_flush(
        self,
        session: Session,
        event: BillingWebhookEvent,
    ) -> BillingWebhookEvent:
        self.added = event

        if self.raise_unique_conflict:
            raise BillingWebhookEventAlreadyExistsError()

        event.created_at = PROVIDER_CREATED_AT
        event.updated_at = PROVIDER_CREATED_AT
        return event


def _persisted_event(
    *,
    raw_body: bytes = RAW_BODY,
    status: BillingWebhookEventStatus = (BillingWebhookEventStatus.RECEIVED),
) -> BillingWebhookEvent:
    event = BillingWebhookEvent(
        id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_event_id=PROVIDER_EVENT_ID,
        event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
        provider_subscription_id=(PROVIDER_SUBSCRIPTION_ID),
        provider_created_at=PROVIDER_CREATED_AT,
        provider_state_version=4,
        payload={
            "id": PROVIDER_EVENT_ID,
        },
        payload_sha256=sha256(raw_body).hexdigest(),
        signature_timestamp=SIGNATURE_TIMESTAMP,
        status=status,
        processing_attempt_count=(1 if status is BillingWebhookEventStatus.PROCESSING else 0),
        processed_at=None,
        failure_code=None,
        failure_message=None,
        correlation_id=CORRELATION_ID,
    )
    event.created_at = PROVIDER_CREATED_AT
    event.updated_at = PROVIDER_CREATED_AT
    return event


def _build_service(
    repository: InMemoryWebhookEventRepository,
) -> IngestBillingWebhookService:
    return IngestBillingWebhookService(
        webhook_event_repository=cast(
            BillingWebhookEventRepository,
            repository,
        )
    )


def _command(
    *,
    raw_body: bytes = RAW_BODY,
) -> IngestBillingWebhookCommand:
    return IngestBillingWebhookCommand(
        provider=BillingProvider.FAKE,
        raw_body=raw_body,
        signature_timestamp=SIGNATURE_TIMESTAMP,
        correlation_id=f" {CORRELATION_ID} ",
    )


def test_service_persists_authenticated_event_without_processing() -> None:
    repository = InMemoryWebhookEventRepository()
    service = _build_service(repository)

    result = service.execute(
        cast(Session, FakeSession()),
        _command(),
    )

    event = repository.added

    assert event is not None
    assert result.webhook_event_id == event.id
    assert result.provider is BillingProvider.FAKE
    assert result.provider_event_id == (PROVIDER_EVENT_ID)
    assert result.status is BillingWebhookEventStatus.RECEIVED
    assert result.duplicate is False

    assert event.provider_subscription_id == (PROVIDER_SUBSCRIPTION_ID)
    assert event.provider_created_at == (PROVIDER_CREATED_AT)
    assert event.provider_state_version == 4
    assert event.payload["id"] == PROVIDER_EVENT_ID
    assert event.payload["type"] == "subscription.renewed"
    assert event.payload_sha256 == sha256(RAW_BODY).hexdigest()
    assert event.signature_timestamp == SIGNATURE_TIMESTAMP
    assert event.correlation_id == CORRELATION_ID
    assert event.status is BillingWebhookEventStatus.RECEIVED
    assert event.processing_attempt_count == 0
    assert event.processed_at is None
    assert event.failure_code is None
    assert event.failure_message is None


def test_identical_existing_event_is_replayed() -> None:
    existing = _persisted_event(status=BillingWebhookEventStatus.PROCESSING)
    repository = InMemoryWebhookEventRepository(existing=existing)
    service = _build_service(repository)

    result = service.execute(
        cast(Session, FakeSession()),
        _command(),
    )

    assert result.webhook_event_id == existing.id
    assert result.status is BillingWebhookEventStatus.PROCESSING
    assert result.duplicate is True
    assert repository.added is None


def test_same_event_id_with_different_raw_payload_conflicts() -> None:
    existing = _persisted_event()
    repository = InMemoryWebhookEventRepository(existing=existing)
    service = _build_service(repository)
    reformatted_body = RAW_BODY.replace(
        b'{"id"',
        b'{ "id"',
        1,
    )

    with pytest.raises(BillingWebhookEventConflictError):
        service.execute(
            cast(Session, FakeSession()),
            _command(raw_body=reformatted_body),
        )

    assert repository.added is None


def test_unique_race_rolls_back_and_reloads_winner() -> None:
    winner = _persisted_event()
    repository = InMemoryWebhookEventRepository(
        concurrent_winner=winner,
        raise_unique_conflict=True,
    )
    service = _build_service(repository)
    session = FakeSession()

    result = service.execute(
        cast(Session, session),
        _command(),
    )

    assert session.rollback_count == 1
    assert repository.lookup_count == 2
    assert result.webhook_event_id == winner.id
    assert result.duplicate is True


def test_unique_race_with_another_payload_conflicts() -> None:
    winner = _persisted_event(raw_body=RAW_BODY + b" ")
    repository = InMemoryWebhookEventRepository(
        concurrent_winner=winner,
        raise_unique_conflict=True,
    )
    service = _build_service(repository)
    session = FakeSession()

    with pytest.raises(BillingWebhookEventConflictError):
        service.execute(
            cast(Session, session),
            _command(),
        )

    assert session.rollback_count == 1


@pytest.mark.parametrize(
    "raw_body",
    [
        b"",
        b"not-json",
        b"{}",
        (
            b'{"id":"evt_01",'
            b'"type":"unsupported.event",'
            b'"created_at":"2026-08-22T12:00:00Z",'
            b'"data":{}}'
        ),
    ],
)
def test_invalid_authenticated_payload_is_rejected(
    raw_body: bytes,
) -> None:
    repository = InMemoryWebhookEventRepository()
    service = _build_service(repository)

    with pytest.raises(BillingWebhookPayloadInvalidError):
        service.execute(
            cast(Session, FakeSession()),
            _command(raw_body=raw_body),
        )

    assert repository.lookup_count == 0
    assert repository.added is None


def test_command_rejects_nonpositive_signature_timestamp() -> None:
    with pytest.raises(
        ValueError,
        match="must be positive",
    ):
        IngestBillingWebhookCommand(
            provider=BillingProvider.FAKE,
            raw_body=RAW_BODY,
            signature_timestamp=0,
        )


def test_command_normalizes_empty_correlation_id() -> None:
    command = IngestBillingWebhookCommand(
        provider=BillingProvider.FAKE,
        raw_body=RAW_BODY,
        signature_timestamp=SIGNATURE_TIMESTAMP,
        correlation_id=" ",
    )

    assert command.correlation_id is None
