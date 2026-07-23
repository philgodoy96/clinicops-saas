from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.audit.context import AuditRecordingContext
from clinicops.billing.enums import (
    BillingProvider,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventNotFoundError,
    BillingWebhookEventProcessingConflictError,
    BillingWebhookEventTerminalFailureError,
)
from clinicops.billing.models import BillingWebhookEvent
from clinicops.billing.repositories import (
    BillingWebhookEventRepository,
)
from clinicops.billing.webhooks.process import (
    ClaimBillingWebhookEventService,
    ProcessBillingWebhookEventCommand,
)

NOW = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)


class RecordingWebhookEventRepository:
    def __init__(
        self,
        event: BillingWebhookEvent | None,
    ) -> None:
        self.event = event
        self.requested_event_id: UUID | None = None
        self.flush_count = 0

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        webhook_event_id: UUID,
    ) -> BillingWebhookEvent | None:
        self.requested_event_id = webhook_event_id

        if self.event is not None and self.event.id == webhook_event_id:
            return self.event

        return None

    def flush(
        self,
        session: Session,
    ) -> None:
        self.flush_count += 1


def _event(
    *,
    status: BillingWebhookEventStatus,
    processing_attempt_count: int,
    failure_code: str | None = None,
    failure_message: str | None = None,
    processed_at: datetime | None = None,
) -> BillingWebhookEvent:
    return BillingWebhookEvent(
        id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_event_id="evt_provider_01",
        event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED.value),
        provider_subscription_id="fake_sub_01",
        provider_created_at=NOW,
        provider_state_version=4,
        payload={
            "id": "evt_provider_01",
        },
        payload_sha256="a" * 64,
        signature_timestamp=int(NOW.timestamp()),
        status=status,
        processing_attempt_count=(processing_attempt_count),
        processed_at=processed_at,
        failure_code=failure_code,
        failure_message=failure_message,
    )


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.worker_system(
        correlation_id="billing-webhook-claim-correlation",
    )


def _command(*, webhook_event_id: UUID) -> ProcessBillingWebhookEventCommand:
    return ProcessBillingWebhookEventCommand(
        webhook_event_id=webhook_event_id,
        audit_context=_audit_context(),
    )


def _service(
    repository: RecordingWebhookEventRepository,
) -> ClaimBillingWebhookEventService:
    return ClaimBillingWebhookEventService(
        webhook_event_repository=cast(
            BillingWebhookEventRepository,
            repository,
        )
    )


@pytest.mark.parametrize(
    (
        "initial_status",
        "initial_attempt_count",
    ),
    [
        (
            BillingWebhookEventStatus.RECEIVED,
            0,
        ),
        (
            BillingWebhookEventStatus.FAILED_RETRYABLE,
            2,
        ),
    ],
)
def test_claim_persists_processing_ownership(
    initial_status: BillingWebhookEventStatus,
    initial_attempt_count: int,
) -> None:
    event = _event(
        status=initial_status,
        processing_attempt_count=(initial_attempt_count),
        failure_code=(
            "subscription_not_found"
            if initial_status is BillingWebhookEventStatus.FAILED_RETRYABLE
            else None
        ),
        failure_message=(
            "Temporary local resolution failure."
            if initial_status is BillingWebhookEventStatus.FAILED_RETRYABLE
            else None
        ),
    )
    repository = RecordingWebhookEventRepository(event)

    claim = _service(repository).execute(
        cast(Session, object()),
        _command(webhook_event_id=event.id),
    )

    assert repository.requested_event_id == event.id
    assert repository.flush_count == 1
    assert event.status is BillingWebhookEventStatus.PROCESSING
    assert event.processing_attempt_count == (initial_attempt_count + 1)
    assert event.processed_at is None
    assert event.failure_code is None
    assert event.failure_message is None

    assert claim.webhook_event_id == event.id
    assert claim.provider is BillingProvider.FAKE
    assert claim.provider_event_id == ("evt_provider_01")
    assert claim.provider_subscription_id == ("fake_sub_01")
    assert claim.provider_state_version == 4
    assert claim.event_type is BillingWebhookEventType.SUBSCRIPTION_RENEWED
    assert claim.status is BillingWebhookEventStatus.PROCESSING
    assert claim.processing_attempt_count == (initial_attempt_count + 1)
    assert claim.replayed is False


@pytest.mark.parametrize(
    "status",
    [
        BillingWebhookEventStatus.PROCESSED,
        BillingWebhookEventStatus.IGNORED,
    ],
)
def test_completed_event_returns_replay_without_mutation(
    status: BillingWebhookEventStatus,
) -> None:
    event = _event(
        status=status,
        processing_attempt_count=2,
        processed_at=NOW,
    )
    repository = RecordingWebhookEventRepository(event)

    claim = _service(repository).execute(
        cast(Session, object()),
        _command(webhook_event_id=event.id),
    )

    assert claim.status is status
    assert claim.processing_attempt_count == 2
    assert claim.replayed is True
    assert repository.flush_count == 0


def test_processing_event_rejects_competing_claim() -> None:
    event = _event(
        status=BillingWebhookEventStatus.PROCESSING,
        processing_attempt_count=1,
    )
    repository = RecordingWebhookEventRepository(event)

    with pytest.raises(BillingWebhookEventProcessingConflictError):
        _service(repository).execute(
            cast(Session, object()),
            _command(webhook_event_id=event.id),
        )

    assert repository.flush_count == 0


def test_terminal_failure_is_replayed_without_mutation() -> None:
    event = _event(
        status=(BillingWebhookEventStatus.FAILED_TERMINAL),
        processing_attempt_count=1,
        failure_code="invalid_period_transition",
        failure_message=("The provider period did not match."),
        processed_at=NOW,
    )
    repository = RecordingWebhookEventRepository(event)

    with pytest.raises(BillingWebhookEventTerminalFailureError) as exception_info:
        _service(repository).execute(
            cast(Session, object()),
            _command(webhook_event_id=event.id),
        )

    assert exception_info.value.failure_code == "invalid_period_transition"
    assert repository.flush_count == 0


def test_terminal_failure_requires_persisted_failure_code() -> None:
    event = _event(
        status=(BillingWebhookEventStatus.FAILED_TERMINAL),
        processing_attempt_count=1,
        processed_at=NOW,
    )
    repository = RecordingWebhookEventRepository(event)

    with pytest.raises(
        RuntimeError,
        match="must persist a failure code",
    ):
        _service(repository).execute(
            cast(Session, object()),
            _command(webhook_event_id=event.id),
        )


def test_missing_event_returns_not_found() -> None:
    repository = RecordingWebhookEventRepository(None)

    with pytest.raises(BillingWebhookEventNotFoundError):
        _service(repository).execute(
            cast(Session, object()),
            _command(webhook_event_id=uuid4()),
        )

    assert repository.flush_count == 0


def test_invalid_persisted_event_type_is_rejected() -> None:
    event = _event(
        status=BillingWebhookEventStatus.RECEIVED,
        processing_attempt_count=0,
    )
    event.event_type = "unsupported.event"
    repository = RecordingWebhookEventRepository(event)

    with pytest.raises(
        RuntimeError,
        match="event type is unsupported",
    ):
        _service(repository).execute(
            cast(Session, object()),
            _command(webhook_event_id=event.id),
        )

    assert repository.flush_count == 1
