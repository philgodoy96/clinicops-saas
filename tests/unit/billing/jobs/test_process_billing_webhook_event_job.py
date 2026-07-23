from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
)
from clinicops.billing.jobs.process_billing_webhook_event import (
    ProcessBillingWebhookEventJobHandler,
)
from clinicops.jobs.contracts import (
    ClaimedBackgroundJob,
    JSONObject,
)
from clinicops.jobs.runtime.exceptions import (
    InvalidJobPayloadError,
    RetryableJobExecutionError,
    TerminalJobExecutionError,
    UnsupportedJobPayloadVersionError,
)


@dataclass
class RecordingWebhookProcessor:
    processed_event_ids: list[UUID] = field(default_factory=list)
    error: Exception | None = None

    def __call__(
        self,
        webhook_event_id: UUID,
    ) -> None:
        self.processed_event_ids.append(webhook_event_id)

        if self.error is not None:
            raise self.error


def _claimed_job(
    *,
    job_type: str = (BILLING_WEBHOOK_PROCESS_JOB_TYPE),
    payload_version: int = 1,
    payload: JSONObject | None = None,
) -> ClaimedBackgroundJob:
    claimed_at = datetime.now(UTC)

    return ClaimedBackgroundJob(
        job_id=uuid4(),
        job_type=job_type,
        payload_version=payload_version,
        payload=(
            payload
            if payload is not None
            else {
                "webhook_event_id": str(uuid4()),
            }
        ),
        processing_attempt_count=1,
        max_attempts=5,
        worker_id="worker-1",
        claim_token=uuid4(),
        claimed_at=claimed_at,
        lease_expires_at=(claimed_at + timedelta(minutes=5)),
        correlation_id=f"correlation-{uuid4()}",
        origin_request_id=f"request-{uuid4()}",
    )


def test_handler_exposes_stable_registry_contract() -> None:
    handler = ProcessBillingWebhookEventJobHandler(RecordingWebhookProcessor())

    assert handler.job_type == "billing.webhook.process"
    assert handler.supported_payload_version == 1


def test_handler_invokes_webhook_processor() -> None:
    webhook_event_id = uuid4()
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    handler.execute(
        _claimed_job(
            payload={
                "webhook_event_id": str(webhook_event_id),
            }
        )
    )

    assert processor.processed_event_ids == [webhook_event_id]


def test_handler_rejects_unexpected_job_type() -> None:
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(TerminalJobExecutionError) as exception_info:
        handler.execute(_claimed_job(job_type=("billing.subscription.reconcile")))

    assert exception_info.value.error_code == "unexpected_billing_job_type"
    assert processor.processed_event_ids == []


def test_handler_rejects_unsupported_payload_version() -> None:
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(UnsupportedJobPayloadVersionError):
        handler.execute(_claimed_job(payload_version=2))

    assert processor.processed_event_ids == []


def test_handler_rejects_invalid_payload() -> None:
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(InvalidJobPayloadError):
        handler.execute(_claimed_job(payload={}))

    assert processor.processed_event_ids == []


def test_handler_preserves_retryable_classification() -> None:
    processor = RecordingWebhookProcessor(
        error=RetryableJobExecutionError(
            "The billing dependency is unavailable.",
            error_code="billing_dependency_unavailable",
        )
    )
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(RetryableJobExecutionError) as exception_info:
        handler.execute(_claimed_job())

    assert exception_info.value.error_code == "billing_dependency_unavailable"


def test_handler_preserves_terminal_classification() -> None:
    processor = RecordingWebhookProcessor(
        error=TerminalJobExecutionError(
            "The billing event cannot be processed.",
            error_code="billing_event_terminal",
        )
    )
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(TerminalJobExecutionError) as exception_info:
        handler.execute(_claimed_job())

    assert exception_info.value.error_code == "billing_event_terminal"


def test_handler_allows_unexpected_error_to_reach_runtime() -> None:
    unexpected_error = RuntimeError("unexpected processor failure")
    processor = RecordingWebhookProcessor(error=unexpected_error)
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(RuntimeError) as exception_info:
        handler.execute(_claimed_job())

    assert exception_info.value is unexpected_error


def test_handler_requires_callable_processor() -> None:
    with pytest.raises(TypeError):
        ProcessBillingWebhookEventJobHandler(
            object()  # type: ignore[arg-type]
        )
