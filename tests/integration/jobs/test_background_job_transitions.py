from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.contracts import (
    CompleteBackgroundJobCommand,
    FailBackgroundJobCommand,
)
from clinicops.jobs.enums import (
    BackgroundJobFailureKind,
    BackgroundJobStatus,
)
from clinicops.jobs.exceptions import (
    BackgroundJobClaimOwnershipError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.services.complete_background_job import (
    CompleteBackgroundJobService,
)
from clinicops.jobs.services.fail_background_job import (
    FailBackgroundJobService,
)


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _processing_job(
    *,
    processing_attempt_count: int = 1,
    max_attempts: int = 5,
) -> BackgroundJob:
    claimed_at = datetime.now(UTC)

    return BackgroundJob(
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(uuid4()),
        },
        status=BackgroundJobStatus.PROCESSING,
        idempotency_key=f"transition-test:{uuid4()}",
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


def _retry_policy() -> BackgroundJobRetryPolicy:
    return BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
        random_value_provider=lambda: 0.0,
    )


def test_complete_processing_job(
    db_session: Session,
) -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    db_session.add(job)
    db_session.flush()

    result = CompleteBackgroundJobService(BackgroundJobRepository(db_session)).execute(
        CompleteBackgroundJobCommand(
            job_id=job.id,
            worker_id="worker-1",
            claim_token=claim_token,
        )
    )

    assert result.status is BackgroundJobStatus.SUCCEEDED
    assert job.status is BackgroundJobStatus.SUCCEEDED
    assert job.completed_at is not None
    assert job.worker_id is None
    assert job.claim_token is None


def test_retryable_failure_schedules_future_attempt(
    db_session: Session,
) -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    db_session.add(job)
    db_session.flush()

    result = FailBackgroundJobService(
        BackgroundJobRepository(db_session),
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

    assert result.status is BackgroundJobStatus.RETRY_SCHEDULED
    assert result.available_at > result.last_failed_at
    assert job.worker_id is None
    assert job.claim_token is None
    assert job.dead_lettered_at is None


def test_terminal_failure_dead_letters_job(
    db_session: Session,
) -> None:
    job = _processing_job()
    claim_token = job.claim_token
    assert claim_token is not None
    db_session.add(job)
    db_session.flush()

    result = FailBackgroundJobService(
        BackgroundJobRepository(db_session),
        _retry_policy(),
    ).execute(
        FailBackgroundJobCommand(
            job_id=job.id,
            worker_id="worker-1",
            claim_token=claim_token,
            failure_kind=(BackgroundJobFailureKind.TERMINAL),
            error_code="unsupported_payload_version",
            error_message="Payload version is unsupported.",
        )
    )

    assert result.status is BackgroundJobStatus.DEAD_LETTERED
    assert result.dead_lettered_at is not None
    assert job.worker_id is None
    assert job.claim_token is None


def test_wrong_claim_token_cannot_complete_job(
    db_session: Session,
) -> None:
    job = _processing_job()
    original_claim_token = job.claim_token

    db_session.add(job)
    db_session.flush()

    with pytest.raises(BackgroundJobClaimOwnershipError):
        CompleteBackgroundJobService(BackgroundJobRepository(db_session)).execute(
            CompleteBackgroundJobCommand(
                job_id=job.id,
                worker_id="worker-1",
                claim_token=uuid4(),
            )
        )

    assert job.status is BackgroundJobStatus.PROCESSING
    assert job.claim_token == original_claim_token


def test_completion_service_does_not_commit() -> None:
    engine = get_engine()
    job = _processing_job()

    try:
        with Session(engine) as seed_session:
            seed_session.add(job)
            seed_session.commit()
            job_id = job.id
            claim_token = job.claim_token

        assert claim_token is not None

        with Session(engine) as transition_session:
            CompleteBackgroundJobService(BackgroundJobRepository(transition_session)).execute(
                CompleteBackgroundJobCommand(
                    job_id=job_id,
                    worker_id="worker-1",
                    claim_token=claim_token,
                )
            )

            with Session(engine) as independent_session:
                persisted = independent_session.get(
                    BackgroundJob,
                    job_id,
                )

                assert persisted is not None
                assert persisted.status is BackgroundJobStatus.PROCESSING
                assert persisted.completed_at is None

            transition_session.rollback()

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.PROCESSING
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id == job.id))
            cleanup_session.commit()
