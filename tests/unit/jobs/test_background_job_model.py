from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import CheckConstraint, Table

from clinicops.jobs.enums import (
    BackgroundJobFailureKind,
    BackgroundJobStatus,
)
from clinicops.jobs.models import BackgroundJob


def test_background_job_status_values_are_stable() -> None:
    assert BackgroundJobStatus.QUEUED.value == "queued"
    assert BackgroundJobStatus.PROCESSING.value == "processing"
    assert BackgroundJobStatus.RETRY_SCHEDULED.value == "retry_scheduled"
    assert BackgroundJobStatus.SUCCEEDED.value == "succeeded"
    assert BackgroundJobStatus.DEAD_LETTERED.value == "dead_lettered"


def test_background_job_failure_kind_values_are_stable() -> None:
    assert BackgroundJobFailureKind.RETRYABLE.value == "retryable"
    assert BackgroundJobFailureKind.TERMINAL.value == "terminal"


def test_background_job_accepts_a_reference_payload() -> None:
    now = datetime.now(UTC)
    webhook_event_id = uuid4()

    job = BackgroundJob(
        id=uuid4(),
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(webhook_event_id),
        },
        status=BackgroundJobStatus.QUEUED,
        idempotency_key=(f"billing-webhook-processing:{webhook_event_id}"),
        priority=0,
        available_at=now,
        processing_attempt_count=0,
        max_attempts=5,
        correlation_id=f"correlation-{uuid4()}",
    )

    assert job.job_type == "billing.webhook.process"
    assert job.payload_version == 1
    assert job.payload == {
        "webhook_event_id": str(webhook_event_id),
    }
    assert job.status is BackgroundJobStatus.QUEUED
    assert job.processing_attempt_count == 0
    assert job.max_attempts == 5


def test_background_job_declares_expected_constraints() -> None:
    table = cast(Table, BackgroundJob.__table__)
    constraint_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert constraint_names == {
        "ck_background_jobs_payload_version_positive",
        ("ck_background_jobs_processing_attempt_count_non_negative"),
        "ck_background_jobs_max_attempts_positive",
        "ck_background_jobs_attempt_count_within_limit",
        "ck_background_jobs_job_type_not_empty",
        "ck_background_jobs_idempotency_key_not_empty",
        "ck_background_jobs_worker_id_not_empty",
        "ck_background_jobs_correlation_id_not_empty",
        "ck_background_jobs_lifecycle_consistency",
    }


def test_background_job_declares_expected_indexes() -> None:
    table = cast(Table, BackgroundJob.__table__)
    index_names = {index.name for index in table.indexes}

    assert index_names == {
        "uq_background_jobs_idempotency_key",
        "ix_background_jobs_available",
        "ix_background_jobs_stale_processing",
    }
