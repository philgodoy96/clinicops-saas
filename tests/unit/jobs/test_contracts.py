from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.jobs.contracts import (
    EnqueueBackgroundJobCommand,
    EnqueuedBackgroundJob,
)
from clinicops.jobs.enums import BackgroundJobStatus


def test_enqueue_command_preserves_job_contract() -> None:
    available_at = datetime.now(UTC)

    command = EnqueueBackgroundJobCommand(
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(uuid4()),
        },
        correlation_id=f"correlation-{uuid4()}",
        idempotency_key=f"background-job:{uuid4()}",
        priority=10,
        available_at=available_at,
        max_attempts=7,
        origin_request_id=f"request-{uuid4()}",
    )

    assert command.job_type == "billing.webhook.process"
    assert command.payload_version == 1
    assert command.priority == 10
    assert command.available_at == available_at
    assert command.max_attempts == 7


def test_enqueue_command_is_immutable() -> None:
    command = EnqueueBackgroundJobCommand(
        job_type="billing.webhook.process",
        payload_version=1,
        payload={},
        correlation_id=f"correlation-{uuid4()}",
    )

    with pytest.raises(FrozenInstanceError):
        command.priority = 10  # type: ignore[misc]


def test_enqueued_job_result_is_immutable() -> None:
    result = EnqueuedBackgroundJob(
        job_id=uuid4(),
        job_type="billing.webhook.process",
        status=BackgroundJobStatus.QUEUED,
        created=True,
        available_at=datetime.now(UTC),
        processing_attempt_count=0,
        max_attempts=5,
    )

    with pytest.raises(FrozenInstanceError):
        result.created = False  # type: ignore[misc]
