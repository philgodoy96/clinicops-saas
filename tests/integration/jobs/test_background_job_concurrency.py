from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.contracts import (
    CompleteBackgroundJobCommand,
    RecoverStaleBackgroundJobsCommand,
)
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import (
    BackgroundJobInvalidTransitionError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.services.complete_background_job import (
    CompleteBackgroundJobService,
)
from clinicops.jobs.services.recover_stale_background_jobs import (
    RecoverStaleBackgroundJobsService,
)


def _processing_job(
    *,
    processing_attempt_count: int = 1,
    max_attempts: int = 5,
) -> BackgroundJob:
    claimed_at = datetime.now(UTC) - timedelta(minutes=10)

    return BackgroundJob(
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(uuid4()),
        },
        status=BackgroundJobStatus.PROCESSING,
        idempotency_key=f"recovery-test:{uuid4()}",
        priority=0,
        available_at=claimed_at,
        processing_attempt_count=processing_attempt_count,
        max_attempts=max_attempts,
        worker_id=f"worker-{uuid4()}",
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


def _recovery_service(
    session: Session,
) -> RecoverStaleBackgroundJobsService:
    return RecoverStaleBackgroundJobsService(
        BackgroundJobRepository(session),
        _retry_policy(),
    )


def test_late_completion_cannot_mutate_recovered_job() -> None:
    engine = get_engine()
    job = _processing_job()

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()

        job_id = job.id
        old_worker_id = job.worker_id
        old_claim_token = job.claim_token

    assert old_worker_id is not None
    assert old_claim_token is not None

    try:
        with Session(engine) as recovery_session:
            result = _recovery_service(recovery_session).execute(
                RecoverStaleBackgroundJobsCommand(
                    batch_size=1,
                )
            )
            recovery_session.commit()

        assert result.recovered_for_retry == (job_id,)

        with (
            Session(engine) as completion_session,
            pytest.raises(BackgroundJobInvalidTransitionError),
        ):
            CompleteBackgroundJobService(BackgroundJobRepository(completion_session)).execute(
                CompleteBackgroundJobCommand(
                    job_id=job_id,
                    worker_id=old_worker_id,
                    claim_token=old_claim_token,
                )
            )

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.RETRY_SCHEDULED
            assert persisted.worker_id is None
            assert persisted.claim_token is None
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id == job_id))
            cleanup_session.commit()


def test_concurrent_recovery_processes_skip_locked_jobs() -> None:
    engine = get_engine()
    jobs = [_processing_job() for _ in range(6)]
    barrier = Barrier(2)

    with Session(engine) as seed_session:
        seed_session.add_all(jobs)
        seed_session.commit()
        job_ids = {job.id for job in jobs}

    def recover() -> tuple[UUID, ...]:
        with Session(engine) as session:
            barrier.wait(timeout=10)

            result = _recovery_service(session).execute(
                RecoverStaleBackgroundJobsCommand(
                    batch_size=3,
                )
            )
            session.commit()

            return result.recovered_for_retry

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(recover)
            second_future = executor.submit(recover)

            first_recovered = first_future.result(timeout=20)
            second_recovered = second_future.result(timeout=20)

        assert len(first_recovered) == 3
        assert len(second_recovered) == 3
        assert set(first_recovered).isdisjoint(second_recovered)
        assert set(first_recovered) | set(second_recovered) == job_ids

        with Session(engine) as verification_session:
            persisted_jobs = (
                verification_session.query(BackgroundJob)
                .filter(BackgroundJob.id.in_(job_ids))
                .all()
            )

            assert len(persisted_jobs) == 6
            assert all(job.status is BackgroundJobStatus.RETRY_SCHEDULED for job in persisted_jobs)
            assert all(
                job.worker_id is None
                and job.claim_token is None
                and job.claimed_at is None
                and job.lease_expires_at is None
                for job in persisted_jobs
            )
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id.in_(job_ids)))
            cleanup_session.commit()


def test_recovery_rollback_preserves_processing_claim() -> None:
    engine = get_engine()
    job = _processing_job()

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()

        job_id = job.id
        original_worker_id = job.worker_id
        original_claim_token = job.claim_token

    try:
        with Session(engine) as recovery_session:
            result = _recovery_service(recovery_session).execute(
                RecoverStaleBackgroundJobsCommand(
                    batch_size=1,
                )
            )

            assert result.recovered_for_retry == (job_id,)
            recovery_session.rollback()

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.PROCESSING
            assert persisted.worker_id == original_worker_id
            assert persisted.claim_token == original_claim_token
            assert persisted.claimed_at is not None
            assert persisted.lease_expires_at is not None
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id == job_id))
            cleanup_session.commit()
