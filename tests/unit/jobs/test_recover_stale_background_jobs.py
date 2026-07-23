from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from clinicops.jobs.contracts import (
    RecoverStaleBackgroundJobsCommand,
)
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import (
    BackgroundJobInvalidConfigurationError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.services.recover_stale_background_jobs import (
    RecoverStaleBackgroundJobsService,
)


class RecordingRecoveryRepository:
    def __init__(
        self,
        *,
        jobs: list[BackgroundJob] | None = None,
        database_time: datetime | None = None,
    ) -> None:
        self.jobs = jobs or []
        self.database_time = database_time or datetime.now(UTC)
        self.requested_batch_size: int | None = None
        self.requested_stale_before: datetime | None = None
        self.flush_count = 0

    def get_database_time(self) -> datetime:
        return self.database_time

    def find_stale_for_update_skip_locked(
        self,
        *,
        batch_size: int,
        stale_before: datetime,
    ) -> list[BackgroundJob]:
        self.requested_batch_size = batch_size
        self.requested_stale_before = stale_before

        stale_jobs = [
            job
            for job in self.jobs
            if (
                job.status is BackgroundJobStatus.PROCESSING
                and job.lease_expires_at is not None
                and job.lease_expires_at <= stale_before
            )
        ]

        return stale_jobs[:batch_size]

    def flush(self) -> None:
        self.flush_count += 1


def _processing_job(
    *,
    lease_expires_at: datetime,
    processing_attempt_count: int = 1,
    max_attempts: int = 5,
) -> BackgroundJob:
    claimed_at = lease_expires_at - timedelta(minutes=5)

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
        lease_expires_at=lease_expires_at,
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


def test_stale_job_is_scheduled_for_retry() -> None:
    database_time = datetime.now(UTC)
    job = _processing_job(lease_expires_at=(database_time - timedelta(seconds=1)))
    old_claim_token = job.claim_token

    repository = RecordingRecoveryRepository(
        jobs=[job],
        database_time=database_time,
    )

    result = RecoverStaleBackgroundJobsService(
        repository,
        _retry_policy(random_value=0.0),
    ).execute(
        RecoverStaleBackgroundJobsCommand(
            batch_size=10,
        )
    )

    assert result.recovered_for_retry == (job.id,)
    assert result.dead_lettered == ()

    assert job.status is BackgroundJobStatus.RETRY_SCHEDULED
    assert job.available_at == (database_time + timedelta(seconds=30))
    assert job.last_error_code == ("processing_lease_expired")
    assert job.last_failed_at == database_time

    assert old_claim_token is not None
    assert job.worker_id is None
    assert job.claim_token is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None

    assert repository.requested_batch_size == 10
    assert repository.requested_stale_before == database_time
    assert repository.flush_count == 1


def test_stale_final_attempt_is_dead_lettered() -> None:
    database_time = datetime.now(UTC)
    job = _processing_job(
        lease_expires_at=(database_time - timedelta(seconds=1)),
        processing_attempt_count=5,
        max_attempts=5,
    )

    repository = RecordingRecoveryRepository(
        jobs=[job],
        database_time=database_time,
    )

    result = RecoverStaleBackgroundJobsService(
        repository,
        _retry_policy(),
    ).execute(
        RecoverStaleBackgroundJobsCommand(
            batch_size=10,
        )
    )

    assert result.recovered_for_retry == ()
    assert result.dead_lettered == (job.id,)

    assert job.status is BackgroundJobStatus.DEAD_LETTERED
    assert job.dead_lettered_at == database_time
    assert job.worker_id is None
    assert job.claim_token is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None


def test_unexpired_processing_job_is_not_recovered() -> None:
    database_time = datetime.now(UTC)
    job = _processing_job(lease_expires_at=(database_time + timedelta(minutes=5)))
    original_claim_token = job.claim_token

    repository = RecordingRecoveryRepository(
        jobs=[job],
        database_time=database_time,
    )

    result = RecoverStaleBackgroundJobsService(
        repository,
        _retry_policy(),
    ).execute(
        RecoverStaleBackgroundJobsCommand(
            batch_size=10,
        )
    )

    assert result.recovered_for_retry == ()
    assert result.dead_lettered == ()
    assert job.status is BackgroundJobStatus.PROCESSING
    assert job.claim_token == original_claim_token
    assert repository.flush_count == 0


def test_recovery_returns_empty_result_when_no_job_is_stale() -> None:
    repository = RecordingRecoveryRepository()

    result = RecoverStaleBackgroundJobsService(
        repository,
        _retry_policy(),
    ).execute(
        RecoverStaleBackgroundJobsCommand(
            batch_size=10,
        )
    )

    assert result.recovered_for_retry == ()
    assert result.dead_lettered == ()
    assert repository.flush_count == 0


@pytest.mark.parametrize(
    "batch_size",
    [
        0,
        -1,
        101,
        True,
    ],
)
def test_recovery_rejects_invalid_batch_size(
    batch_size: int,
) -> None:
    repository = RecordingRecoveryRepository()

    with pytest.raises(BackgroundJobInvalidConfigurationError):
        RecoverStaleBackgroundJobsService(
            repository,
            _retry_policy(),
        ).execute(
            RecoverStaleBackgroundJobsCommand(
                batch_size=batch_size,
            )
        )
