from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from clinicops.jobs.contracts import ClaimBackgroundJobsCommand
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import (
    BackgroundJobInvalidConfigurationError,
    BackgroundJobInvalidTransitionError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.services.claim_background_jobs import (
    ClaimBackgroundJobsService,
)


class RecordingClaimRepository:
    def __init__(
        self,
        *,
        jobs: list[BackgroundJob] | None = None,
        database_time: datetime | None = None,
    ) -> None:
        self.jobs = jobs or []
        self.database_time = database_time or datetime.now(UTC)
        self.requested_batch_size: int | None = None
        self.flush_count = 0

    def get_database_time(self) -> datetime:
        return self.database_time

    def claim_available_for_update_skip_locked(
        self,
        *,
        batch_size: int,
    ) -> list[BackgroundJob]:
        self.requested_batch_size = batch_size
        return self.jobs[:batch_size]

    def flush(self) -> None:
        self.flush_count += 1


def _job(
    *,
    status: BackgroundJobStatus = (BackgroundJobStatus.QUEUED),
    processing_attempt_count: int = 0,
    max_attempts: int = 5,
) -> BackgroundJob:
    return BackgroundJob(
        id=uuid4(),
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(uuid4()),
        },
        status=status,
        priority=0,
        available_at=datetime.now(UTC),
        processing_attempt_count=processing_attempt_count,
        max_attempts=max_attempts,
        correlation_id=f"correlation-{uuid4()}",
    )


def test_claim_marks_jobs_as_processing() -> None:
    database_time = datetime.now(UTC)
    first = _job()
    second = _job(
        status=BackgroundJobStatus.RETRY_SCHEDULED,
        processing_attempt_count=1,
    )
    repository = RecordingClaimRepository(
        jobs=[first, second],
        database_time=database_time,
    )
    service = ClaimBackgroundJobsService(repository)

    claims = service.execute(
        ClaimBackgroundJobsCommand(
            worker_id="worker-1",
            batch_size=2,
            lease_duration=timedelta(minutes=5),
        )
    )

    assert len(claims) == 2
    assert repository.requested_batch_size == 2
    assert repository.flush_count == 1

    assert first.status is BackgroundJobStatus.PROCESSING
    assert first.processing_attempt_count == 1
    assert first.worker_id == "worker-1"
    assert first.claim_token is not None
    assert first.claimed_at == database_time
    assert first.lease_expires_at == database_time + timedelta(minutes=5)

    assert second.status is BackgroundJobStatus.PROCESSING
    assert second.processing_attempt_count == 2
    assert second.worker_id == "worker-1"
    assert second.claim_token is not None

    assert first.claim_token != second.claim_token


def test_claim_returns_immutable_execution_contract() -> None:
    job = _job()
    repository = RecordingClaimRepository(jobs=[job])
    service = ClaimBackgroundJobsService(repository)

    claims = service.execute(
        ClaimBackgroundJobsCommand(
            worker_id="worker-1",
            batch_size=1,
            lease_duration=timedelta(minutes=5),
        )
    )

    claim = claims[0]

    assert claim.job_id == job.id
    assert claim.job_type == job.job_type
    assert claim.payload_version == 1
    assert claim.payload == job.payload
    assert claim.processing_attempt_count == 1
    assert claim.max_attempts == 5
    assert claim.worker_id == "worker-1"
    assert claim.claim_token == job.claim_token
    assert claim.claimed_at == job.claimed_at
    assert claim.lease_expires_at == job.lease_expires_at
    assert claim.correlation_id == job.correlation_id


def test_claim_normalizes_worker_id() -> None:
    job = _job()
    repository = RecordingClaimRepository(jobs=[job])
    service = ClaimBackgroundJobsService(repository)

    claims = service.execute(
        ClaimBackgroundJobsCommand(
            worker_id="  worker-1  ",
            batch_size=1,
            lease_duration=timedelta(minutes=5),
        )
    )

    assert claims[0].worker_id == "worker-1"
    assert job.worker_id == "worker-1"


def test_claim_returns_empty_tuple_when_no_job_is_available() -> None:
    repository = RecordingClaimRepository()
    service = ClaimBackgroundJobsService(repository)

    claims = service.execute(
        ClaimBackgroundJobsCommand(
            worker_id="worker-1",
            batch_size=10,
            lease_duration=timedelta(minutes=5),
        )
    )

    assert claims == ()
    assert repository.flush_count == 0


@pytest.mark.parametrize(
    "command",
    [
        ClaimBackgroundJobsCommand(
            worker_id=" ",
            batch_size=1,
            lease_duration=timedelta(minutes=5),
        ),
        ClaimBackgroundJobsCommand(
            worker_id="worker-1",
            batch_size=0,
            lease_duration=timedelta(minutes=5),
        ),
        ClaimBackgroundJobsCommand(
            worker_id="worker-1",
            batch_size=101,
            lease_duration=timedelta(minutes=5),
        ),
        ClaimBackgroundJobsCommand(
            worker_id="worker-1",
            batch_size=1,
            lease_duration=timedelta(0),
        ),
        ClaimBackgroundJobsCommand(
            worker_id="worker-1",
            batch_size=1,
            lease_duration=timedelta(days=2),
        ),
    ],
)
def test_claim_rejects_invalid_configuration(
    command: ClaimBackgroundJobsCommand,
) -> None:
    repository = RecordingClaimRepository()
    service = ClaimBackgroundJobsService(repository)

    with pytest.raises(BackgroundJobInvalidConfigurationError):
        service.execute(command)


def test_claim_rejects_non_claimable_status() -> None:
    job = _job(status=BackgroundJobStatus.PROCESSING)
    repository = RecordingClaimRepository(jobs=[job])
    service = ClaimBackgroundJobsService(repository)

    with pytest.raises(BackgroundJobInvalidTransitionError):
        service.execute(
            ClaimBackgroundJobsCommand(
                worker_id="worker-1",
                batch_size=1,
                lease_duration=timedelta(minutes=5),
            )
        )


def test_claim_rejects_exhausted_attempt_count() -> None:
    job = _job(
        processing_attempt_count=5,
        max_attempts=5,
    )
    repository = RecordingClaimRepository(jobs=[job])
    service = ClaimBackgroundJobsService(repository)

    with pytest.raises(BackgroundJobInvalidTransitionError):
        service.execute(
            ClaimBackgroundJobsCommand(
                worker_id="worker-1",
                batch_size=1,
                lease_duration=timedelta(minutes=5),
            )
        )
