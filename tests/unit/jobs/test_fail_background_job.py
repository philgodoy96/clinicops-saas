from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from clinicops.jobs.contracts import FailBackgroundJobCommand
from clinicops.jobs.enums import (
    BackgroundJobFailureKind,
    BackgroundJobStatus,
)
from clinicops.jobs.exceptions import (
    BackgroundJobClaimOwnershipError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.services.fail_background_job import (
    FailBackgroundJobService,
)


class RecordingFailureRepository:
    def __init__(
        self,
        *,
        job: BackgroundJob,
        database_time: datetime,
    ) -> None:
        self.job = job
        self.database_time = database_time
        self.requested_job_id: UUID | None = None
        self.flush_count = 0

    def get_by_id_for_update(
        self,
        job_id: UUID,
    ) -> BackgroundJob | None:
        self.requested_job_id = job_id
        return self.job

    def get_database_time(self) -> datetime:
        return self.database_time

    def flush(self) -> None:
        self.flush_count += 1


def _processing_job(
    *,
    processing_attempt_count: int = 1,
    max_attempts: int = 5,
) -> BackgroundJob:
    claimed_at = datetime.now(UTC)

    return BackgroundJob(
        id=uuid4(),
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(uuid4()),
        },
        status=BackgroundJobStatus.PROCESSING,
        priority=0,
        available_at=claimed_at,
        processing_attempt_count=processing_attempt_count,
        max_attempts=max_attempts,
        worker_id="worker-1",
        claim_token=uuid4(),
        claimed_at=claimed_at,
        lease_expires_at=(claimed_at + timedelta(minutes=5)),
        correlation_id=f"correlation-{uuid4()}",
    )


def _retry_policy(
    *,
    random_value: float = 0.0,
) -> BackgroundJobRetryPolicy:
    return BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
        random_value_provider=lambda: random_value,
    )


def test_retryable_failure_schedules_retry() -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    failed_at = datetime.now(UTC)
    repository = RecordingFailureRepository(
        job=job,
        database_time=failed_at,
    )
    service = FailBackgroundJobService(
        repository,
        _retry_policy(random_value=0.0),
    )

    result = service.execute(
        FailBackgroundJobCommand(
            job_id=job.id,
            worker_id="worker-1",
            claim_token=claim_token,
            failure_kind=(BackgroundJobFailureKind.RETRYABLE),
            error_code="provider_unavailable",
            error_message="Provider unavailable.",
        )
    )

    assert result.status is BackgroundJobStatus.RETRY_SCHEDULED
    assert result.available_at == (failed_at + timedelta(seconds=30))
    assert result.dead_lettered_at is None

    assert job.status is BackgroundJobStatus.RETRY_SCHEDULED
    assert job.worker_id is None
    assert job.claim_token is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None
    assert job.last_error_code == "provider_unavailable"
    assert job.last_error_message == "Provider unavailable."
    assert job.last_failed_at == failed_at
    assert repository.flush_count == 1


def test_terminal_failure_dead_letters_immediately() -> None:
    job = _processing_job(
        processing_attempt_count=1,
        max_attempts=5,
    )
    claim_token = job.claim_token
    assert claim_token is not None
    failed_at = datetime.now(UTC)
    repository = RecordingFailureRepository(
        job=job,
        database_time=failed_at,
    )

    result = FailBackgroundJobService(
        repository,
        _retry_policy(),
    ).execute(
        FailBackgroundJobCommand(
            job_id=job.id,
            worker_id="worker-1",
            claim_token=claim_token,
            failure_kind=(BackgroundJobFailureKind.TERMINAL),
            error_code="invalid_payload",
            error_message="Payload version is unsupported.",
        )
    )

    assert result.status is BackgroundJobStatus.DEAD_LETTERED
    assert result.dead_lettered_at == failed_at
    assert job.status is BackgroundJobStatus.DEAD_LETTERED
    assert job.dead_lettered_at == failed_at


def test_retryable_failure_dead_letters_at_maximum_attempts() -> None:
    job = _processing_job(
        processing_attempt_count=5,
        max_attempts=5,
    )
    claim_token = job.claim_token
    assert claim_token is not None
    failed_at = datetime.now(UTC)
    repository = RecordingFailureRepository(
        job=job,
        database_time=failed_at,
    )

    result = FailBackgroundJobService(
        repository,
        _retry_policy(),
    ).execute(
        FailBackgroundJobCommand(
            job_id=job.id,
            worker_id="worker-1",
            claim_token=claim_token,
            failure_kind=(BackgroundJobFailureKind.RETRYABLE),
            error_code="provider_unavailable",
            error_message="Provider unavailable.",
        )
    )

    assert result.status is BackgroundJobStatus.DEAD_LETTERED
    assert result.dead_lettered_at == failed_at


def test_failure_message_is_sanitized_and_bounded() -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    repository = RecordingFailureRepository(
        job=job,
        database_time=datetime.now(UTC),
    )
    long_message = "  provider\nfailure\t" + ("x" * 3000)

    FailBackgroundJobService(
        repository,
        _retry_policy(),
    ).execute(
        FailBackgroundJobCommand(
            job_id=job.id,
            worker_id="worker-1",
            claim_token=claim_token,
            failure_kind=(BackgroundJobFailureKind.RETRYABLE),
            error_code="  provider_unavailable  ",
            error_message=long_message,
        )
    )

    assert job.last_error_code == "provider_unavailable"
    assert job.last_error_message is not None
    assert "\n" not in job.last_error_message
    assert "\t" not in job.last_error_message
    assert len(job.last_error_message) == 2000


def test_failure_rejects_claim_ownership_mismatch() -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    repository = RecordingFailureRepository(
        job=job,
        database_time=datetime.now(UTC),
    )

    with pytest.raises(BackgroundJobClaimOwnershipError):
        FailBackgroundJobService(
            repository,
            _retry_policy(),
        ).execute(
            FailBackgroundJobCommand(
                job_id=job.id,
                worker_id="different-worker",
                claim_token=claim_token,
                failure_kind=(BackgroundJobFailureKind.RETRYABLE),
                error_code="provider_unavailable",
                error_message="Provider unavailable.",
            )
        )

    assert job.status is BackgroundJobStatus.PROCESSING
    assert repository.flush_count == 0
