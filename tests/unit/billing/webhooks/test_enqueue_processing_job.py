from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
    BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION,
)
from clinicops.billing.webhooks.enqueue_processing_job import (
    EnqueueBillingWebhookProcessingJobCommand,
    EnqueueBillingWebhookProcessingJobService,
)
from clinicops.jobs.contracts import (
    EnqueueBackgroundJobCommand,
    EnqueuedBackgroundJob,
)


class RecordingBackgroundJobEnqueuer:
    def __init__(
        self,
        *,
        job_id: UUID | None = None,
        created: bool = True,
    ) -> None:
        self.job_id = job_id if job_id is not None else uuid4()
        self.created = created
        self.calls: list[
            tuple[
                Session,
                EnqueueBackgroundJobCommand,
            ]
        ] = []

    def __call__(
        self,
        session: Session,
        command: EnqueueBackgroundJobCommand,
    ) -> EnqueuedBackgroundJob:
        self.calls.append((session, command))

        return cast(
            EnqueuedBackgroundJob,
            SimpleNamespace(
                job_id=self.job_id,
                created=self.created,
            ),
        )


def _session_double() -> Session:
    return cast(Session, object())


def test_service_builds_billing_webhook_job_contract() -> None:
    webhook_event_id = uuid4()
    session = _session_double()
    enqueuer = RecordingBackgroundJobEnqueuer()
    service = EnqueueBillingWebhookProcessingJobService(enqueuer)

    result = service.execute(
        session,
        EnqueueBillingWebhookProcessingJobCommand(
            webhook_event_id=webhook_event_id,
            correlation_id="correlation-123",
            origin_request_id="request-456",
        ),
    )

    assert len(enqueuer.calls) == 1

    recorded_session, command = enqueuer.calls[0]

    assert recorded_session is session
    assert command.job_type == BILLING_WEBHOOK_PROCESS_JOB_TYPE
    assert command.payload_version == BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION
    assert command.payload == {
        "webhook_event_id": str(webhook_event_id),
    }
    assert command.idempotency_key == (f"billing-webhook-process:{webhook_event_id}")
    assert command.correlation_id == "correlation-123"
    assert command.origin_request_id == "request-456"

    assert result.webhook_event_id == webhook_event_id
    assert result.job_id == enqueuer.job_id
    assert result.created is True


def test_service_preserves_reused_job_result() -> None:
    webhook_event_id = uuid4()
    existing_job_id = uuid4()
    enqueuer = RecordingBackgroundJobEnqueuer(
        job_id=existing_job_id,
        created=False,
    )
    service = EnqueueBillingWebhookProcessingJobService(enqueuer)

    result = service.execute(
        _session_double(),
        EnqueueBillingWebhookProcessingJobCommand(
            webhook_event_id=webhook_event_id,
            correlation_id="correlation-123",
        ),
    )

    assert result.webhook_event_id == webhook_event_id
    assert result.job_id == existing_job_id
    assert result.created is False


def test_service_allows_missing_origin_request_id() -> None:
    enqueuer = RecordingBackgroundJobEnqueuer()
    service = EnqueueBillingWebhookProcessingJobService(enqueuer)

    service.execute(
        _session_double(),
        EnqueueBillingWebhookProcessingJobCommand(
            webhook_event_id=uuid4(),
            correlation_id="correlation-123",
        ),
    )

    _, command = enqueuer.calls[0]

    assert command.origin_request_id is None


def test_service_derives_stable_idempotency_key() -> None:
    webhook_event_id = uuid4()
    enqueuer = RecordingBackgroundJobEnqueuer()
    service = EnqueueBillingWebhookProcessingJobService(enqueuer)

    command = EnqueueBillingWebhookProcessingJobCommand(
        webhook_event_id=webhook_event_id,
        correlation_id="correlation-123",
        origin_request_id="request-456",
    )

    service.execute(_session_double(), command)
    service.execute(_session_double(), command)

    first_job_command = enqueuer.calls[0][1]
    second_job_command = enqueuer.calls[1][1]

    assert (
        first_job_command.idempotency_key
        == second_job_command.idempotency_key
        == (f"billing-webhook-process:{webhook_event_id}")
    )


def test_service_rejects_non_uuid_event_id() -> None:
    service = EnqueueBillingWebhookProcessingJobService(RecordingBackgroundJobEnqueuer())

    with pytest.raises(
        TypeError,
        match="webhook_event_id must be a UUID",
    ):
        service.execute(
            _session_double(),
            EnqueueBillingWebhookProcessingJobCommand(
                webhook_event_id=cast(
                    UUID,
                    "not-a-uuid",
                ),
                correlation_id="correlation-123",
            ),
        )


def test_service_requires_callable_enqueuer() -> None:
    with pytest.raises(
        TypeError,
        match=("enqueue_background_job must be callable"),
    ):
        EnqueueBillingWebhookProcessingJobService(
            object(),  # type: ignore[arg-type]
        )
