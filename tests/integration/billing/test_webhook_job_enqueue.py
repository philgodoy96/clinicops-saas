from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
    BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION,
)
from clinicops.billing.webhooks.enqueue_processing_job import (
    EnqueueBillingWebhookProcessingJobCommand,
    EnqueueBillingWebhookProcessingJobService,
)
from clinicops.db.session import get_engine
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.models import BackgroundJob


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_service_persists_billing_webhook_job_contract(
    db_session: Session,
) -> None:
    webhook_event_id = uuid4()
    command = EnqueueBillingWebhookProcessingJobCommand(
        webhook_event_id=webhook_event_id,
        correlation_id="correlation-integration",
        origin_request_id="request-integration",
    )

    result = EnqueueBillingWebhookProcessingJobService().execute(db_session, command)

    job = db_session.get(
        BackgroundJob,
        result.job_id,
    )

    assert job is not None
    assert result.webhook_event_id == webhook_event_id
    assert result.created is True

    assert job.job_type == BILLING_WEBHOOK_PROCESS_JOB_TYPE
    assert job.payload_version == BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION
    assert job.payload == {
        "webhook_event_id": str(webhook_event_id),
    }
    assert job.idempotency_key == (f"billing-webhook-process:{webhook_event_id}")
    assert job.status is BackgroundJobStatus.QUEUED
    assert job.correlation_id == "correlation-integration"
    assert job.origin_request_id == "request-integration"


def test_equivalent_enqueue_reuses_existing_job(
    db_session: Session,
) -> None:
    webhook_event_id = uuid4()
    command = EnqueueBillingWebhookProcessingJobCommand(
        webhook_event_id=webhook_event_id,
        correlation_id="correlation-replay",
        origin_request_id="request-replay",
    )
    service = EnqueueBillingWebhookProcessingJobService()

    first_result = service.execute(
        db_session,
        command,
    )
    second_result = service.execute(
        db_session,
        command,
    )

    assert first_result.created is True
    assert second_result.created is False
    assert second_result.job_id == first_result.job_id

    job_count = db_session.scalar(
        select(func.count())
        .select_from(BackgroundJob)
        .where(BackgroundJob.idempotency_key == (f"billing-webhook-process:{webhook_event_id}"))
    )

    assert job_count == 1


def test_service_does_not_commit_caller_transaction(
    db_session: Session,
) -> None:
    result = EnqueueBillingWebhookProcessingJobService().execute(
        db_session,
        EnqueueBillingWebhookProcessingJobCommand(
            webhook_event_id=uuid4(),
            correlation_id=("correlation-uncommitted"),
            origin_request_id=("request-uncommitted"),
        ),
    )

    with Session(get_engine()) as independent_session:
        independently_visible_job = independent_session.get(
            BackgroundJob,
            result.job_id,
        )

    assert independently_visible_job is None


def test_outer_rollback_removes_enqueued_job(
    db_session: Session,
) -> None:
    result = EnqueueBillingWebhookProcessingJobService().execute(
        db_session,
        EnqueueBillingWebhookProcessingJobCommand(
            webhook_event_id=uuid4(),
            correlation_id=("correlation-rollback"),
            origin_request_id="request-rollback",
        ),
    )

    db_session.rollback()

    with Session(get_engine()) as independent_session:
        persisted_job = independent_session.get(
            BackgroundJob,
            result.job_id,
        )

    assert persisted_job is None
