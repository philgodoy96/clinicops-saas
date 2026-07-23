from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
    BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION,
)
from clinicops.jobs.contracts import (
    EnqueueBackgroundJobCommand,
    EnqueuedBackgroundJob,
)
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.services.enqueue_background_job import (
    EnqueueBackgroundJobService,
)

_IDEMPOTENCY_KEY_PREFIX = "billing-webhook-process"

type BackgroundJobEnqueuer = Callable[
    [Session, EnqueueBackgroundJobCommand],
    EnqueuedBackgroundJob,
]


@dataclass(frozen=True, slots=True)
class EnqueueBillingWebhookProcessingJobCommand:
    webhook_event_id: UUID
    correlation_id: str
    origin_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class EnqueuedBillingWebhookProcessingJob:
    webhook_event_id: UUID
    job_id: UUID
    created: bool


class EnqueueBillingWebhookProcessingJobService:
    def __init__(
        self,
        enqueue_background_job: (BackgroundJobEnqueuer | None) = None,
    ) -> None:
        if enqueue_background_job is not None and not callable(enqueue_background_job):
            raise TypeError("enqueue_background_job must be callable.")

        self._enqueue_background_job = (
            enqueue_background_job
            if enqueue_background_job is not None
            else _enqueue_background_job
        )

    def execute(
        self,
        session: Session,
        command: (EnqueueBillingWebhookProcessingJobCommand),
    ) -> EnqueuedBillingWebhookProcessingJob:
        if not isinstance(command.webhook_event_id, UUID):
            raise TypeError("webhook_event_id must be a UUID.")

        result = self._enqueue_background_job(
            session,
            EnqueueBackgroundJobCommand(
                job_type=(BILLING_WEBHOOK_PROCESS_JOB_TYPE),
                payload_version=(BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION),
                payload={
                    "webhook_event_id": str(command.webhook_event_id),
                },
                correlation_id=command.correlation_id,
                idempotency_key=(f"{_IDEMPOTENCY_KEY_PREFIX}:{command.webhook_event_id}"),
                origin_request_id=(command.origin_request_id),
            ),
        )

        return EnqueuedBillingWebhookProcessingJob(
            webhook_event_id=command.webhook_event_id,
            job_id=result.job_id,
            created=result.created,
        )


def _enqueue_background_job(
    session: Session,
    command: EnqueueBackgroundJobCommand,
) -> EnqueuedBackgroundJob:
    return EnqueueBackgroundJobService(BackgroundJobRepository(session)).execute(command)


__all__ = [
    "BackgroundJobEnqueuer",
    "EnqueueBillingWebhookProcessingJobCommand",
    "EnqueueBillingWebhookProcessingJobService",
    "EnqueuedBillingWebhookProcessingJob",
]
