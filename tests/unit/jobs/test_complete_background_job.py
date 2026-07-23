from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from clinicops.jobs.contracts import (
    CompleteBackgroundJobCommand,
)
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import (
    BackgroundJobClaimOwnershipError,
    BackgroundJobInvalidTransitionError,
    BackgroundJobNotFoundError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.services.complete_background_job import (
    CompleteBackgroundJobService,
)


class RecordingCompletionRepository:
    def __init__(
        self,
        *,
        job: BackgroundJob | None,
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


def _processing_job() -> BackgroundJob:
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
        processing_attempt_count=2,
        max_attempts=5,
        worker_id="worker-1",
        claim_token=uuid4(),
        claimed_at=claimed_at,
        lease_expires_at=(claimed_at + timedelta(minutes=5)),
        correlation_id=f"correlation-{uuid4()}",
    )


def test_complete_marks_valid_claim_as_succeeded() -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    completed_at = datetime.now(UTC)
    repository = RecordingCompletionRepository(
        job=job,
        database_time=completed_at,
    )
    service = CompleteBackgroundJobService(repository)

    result = service.execute(
        CompleteBackgroundJobCommand(
            job_id=job.id,
            worker_id="worker-1",
            claim_token=claim_token,
        )
    )

    assert result.job_id == job.id
    assert result.status is BackgroundJobStatus.SUCCEEDED
    assert result.completed_at == completed_at
    assert result.processing_attempt_count == 2

    assert job.status is BackgroundJobStatus.SUCCEEDED
    assert job.completed_at == completed_at
    assert job.worker_id is None
    assert job.claim_token is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None
    assert repository.flush_count == 1


def test_complete_preserves_previous_failure_history() -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    previous_failure = datetime.now(UTC) - timedelta(minutes=2)
    job.last_error_code = "temporary_provider_error"
    job.last_error_message = "Provider temporarily unavailable."
    job.last_failed_at = previous_failure

    repository = RecordingCompletionRepository(
        job=job,
        database_time=datetime.now(UTC),
    )

    CompleteBackgroundJobService(repository).execute(
        CompleteBackgroundJobCommand(
            job_id=job.id,
            worker_id="worker-1",
            claim_token=claim_token,
        )
    )

    assert job.last_error_code == "temporary_provider_error"
    assert job.last_error_message == "Provider temporarily unavailable."
    assert job.last_failed_at == previous_failure


def test_complete_rejects_unknown_job() -> None:
    job_id = uuid4()
    repository = RecordingCompletionRepository(
        job=None,
        database_time=datetime.now(UTC),
    )

    with pytest.raises(BackgroundJobNotFoundError):
        CompleteBackgroundJobService(repository).execute(
            CompleteBackgroundJobCommand(
                job_id=job_id,
                worker_id="worker-1",
                claim_token=uuid4(),
            )
        )


def test_complete_rejects_non_processing_job() -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    job.status = BackgroundJobStatus.RETRY_SCHEDULED

    repository = RecordingCompletionRepository(
        job=job,
        database_time=datetime.now(UTC),
    )

    with pytest.raises(BackgroundJobInvalidTransitionError):
        CompleteBackgroundJobService(repository).execute(
            CompleteBackgroundJobCommand(
                job_id=job.id,
                worker_id="worker-1",
                claim_token=claim_token,
            )
        )


@pytest.mark.parametrize(
    ("worker_id", "claim_token"),
    [
        (
            "different-worker",
            None,
        ),
        (
            "worker-1",
            uuid4(),
        ),
    ],
)
def test_complete_rejects_claim_ownership_mismatch(
    worker_id: str,
    claim_token: UUID | None,
) -> None:
    job = _processing_job()
    provided_claim_token = claim_token or job.claim_token

    assert provided_claim_token is not None

    repository = RecordingCompletionRepository(
        job=job,
        database_time=datetime.now(UTC),
    )

    with pytest.raises(BackgroundJobClaimOwnershipError):
        CompleteBackgroundJobService(repository).execute(
            CompleteBackgroundJobCommand(
                job_id=job.id,
                worker_id=worker_id,
                claim_token=provided_claim_token,
            )
        )

    assert job.status is BackgroundJobStatus.PROCESSING
    assert repository.flush_count == 0
