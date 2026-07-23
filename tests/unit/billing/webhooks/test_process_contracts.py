from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from clinicops.audit.context import AuditRecordingContext
from clinicops.billing.enums import (
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    BillingWebhookProcessingOutcome,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventNotFoundError,
    BillingWebhookEventProcessingConflictError,
    BillingWebhookEventTerminalFailureError,
)
from clinicops.billing.webhooks.process import (
    ProcessBillingWebhookEventCommand,
    ProcessedBillingWebhookEvent,
)


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.worker_system(
        correlation_id="billing-webhook-correlation-id",
        request_id="billing-webhook-request-id",
    )


def test_processing_command_is_immutable() -> None:
    command = ProcessBillingWebhookEventCommand(
        webhook_event_id=uuid4(),
        audit_context=_audit_context(),
    )

    with pytest.raises(FrozenInstanceError):
        command.webhook_event_id = uuid4()  # type: ignore[misc]


def test_processing_command_requires_uuid_event_id() -> None:
    with pytest.raises(
        TypeError,
        match="must be a UUID",
    ):
        ProcessBillingWebhookEventCommand(
            webhook_event_id="not-a-uuid",  # type: ignore[arg-type]
            audit_context=_audit_context(),
        )


def test_processing_command_requires_audit_context() -> None:
    context = _audit_context()
    command = ProcessBillingWebhookEventCommand(
        webhook_event_id=uuid4(),
        audit_context=context,
    )

    assert command.audit_context is context

    with pytest.raises(FrozenInstanceError):
        command.audit_context = _audit_context()  # type: ignore[misc]

    with pytest.raises(
        TypeError,
        match="AuditRecordingContext",
    ):
        ProcessBillingWebhookEventCommand(
            webhook_event_id=uuid4(),
            audit_context="not-a-context",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (
            BillingWebhookProcessingOutcome.APPLIED,
            BillingWebhookEventStatus.PROCESSED,
        ),
        (
            BillingWebhookProcessingOutcome.IGNORED,
            BillingWebhookEventStatus.IGNORED,
        ),
    ],
)
def test_processing_result_accepts_consistent_outcome(
    outcome: BillingWebhookProcessingOutcome,
    status: BillingWebhookEventStatus,
) -> None:
    webhook_event_id = uuid4()
    subscription_id = uuid4()

    result = ProcessedBillingWebhookEvent(
        webhook_event_id=webhook_event_id,
        provider_event_id=" evt_provider_01 ",
        event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED),
        status=status,
        outcome=outcome,
        subscription_id=subscription_id,
        processing_attempt_count=2,
    )

    assert result.webhook_event_id == webhook_event_id
    assert result.provider_event_id == "evt_provider_01"
    assert result.subscription_id == subscription_id
    assert result.processing_attempt_count == 2


def test_processing_result_is_immutable() -> None:
    result = ProcessedBillingWebhookEvent(
        webhook_event_id=uuid4(),
        provider_event_id="evt_provider_01",
        event_type=(BillingWebhookEventType.SUBSCRIPTION_CANCELED),
        status=BillingWebhookEventStatus.PROCESSED,
        outcome=BillingWebhookProcessingOutcome.APPLIED,
        subscription_id=uuid4(),
        processing_attempt_count=1,
    )

    with pytest.raises(FrozenInstanceError):
        result.processing_attempt_count = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (
            BillingWebhookProcessingOutcome.APPLIED,
            BillingWebhookEventStatus.IGNORED,
        ),
        (
            BillingWebhookProcessingOutcome.IGNORED,
            BillingWebhookEventStatus.PROCESSED,
        ),
    ],
)
def test_processing_result_rejects_status_outcome_mismatch(
    outcome: BillingWebhookProcessingOutcome,
    status: BillingWebhookEventStatus,
) -> None:
    with pytest.raises(
        ValueError,
        match="does not match",
    ):
        ProcessedBillingWebhookEvent(
            webhook_event_id=uuid4(),
            provider_event_id="evt_provider_01",
            event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED),
            status=status,
            outcome=outcome,
            subscription_id=uuid4(),
            processing_attempt_count=1,
        )


@pytest.mark.parametrize(
    "processing_attempt_count",
    [
        0,
        -1,
    ],
)
def test_processing_result_requires_positive_attempt_count(
    processing_attempt_count: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="at least one attempt",
    ):
        ProcessedBillingWebhookEvent(
            webhook_event_id=uuid4(),
            provider_event_id="evt_provider_01",
            event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED),
            status=BillingWebhookEventStatus.PROCESSED,
            outcome=BillingWebhookProcessingOutcome.APPLIED,
            subscription_id=uuid4(),
            processing_attempt_count=(processing_attempt_count),
        )


def test_processing_result_rejects_empty_provider_event_id() -> None:
    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        ProcessedBillingWebhookEvent(
            webhook_event_id=uuid4(),
            provider_event_id=" ",
            event_type=(BillingWebhookEventType.SUBSCRIPTION_RENEWED),
            status=BillingWebhookEventStatus.PROCESSED,
            outcome=BillingWebhookProcessingOutcome.APPLIED,
            subscription_id=uuid4(),
            processing_attempt_count=1,
        )


def test_processing_errors_have_stable_public_codes() -> None:
    terminal_error = BillingWebhookEventTerminalFailureError(
        failure_code="invalid_period_transition",
    )

    assert BillingWebhookEventNotFoundError.code == "billing_webhook_event_not_found"
    assert (
        BillingWebhookEventProcessingConflictError.code
        == "billing_webhook_event_processing_conflict"
    )
    assert terminal_error.code == "billing_webhook_event_terminal_failure"
    assert terminal_error.failure_code == "invalid_period_transition"
    assert "invalid_period_transition" not in (terminal_error.public_message)
