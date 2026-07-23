from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.contracts import (
    ClaimBackgroundJobsCommand,
    CompleteBackgroundJobCommand,
    FailBackgroundJobCommand,
    RecoveredBackgroundJobs,
    RecoverStaleBackgroundJobsCommand,
)
from clinicops.jobs.enums import (
    BackgroundJobFailureKind,
    BackgroundJobStatus,
)
from clinicops.jobs.exceptions import (
    BackgroundJobInvalidTransitionError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.services.claim_background_jobs import (
    ClaimBackgroundJobsService,
)
from clinicops.jobs.services.complete_background_job import (
    CompleteBackgroundJobService,
)
from clinicops.jobs.services.fail_background_job import (
    FailBackgroundJobService,
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


def test_claim_does_not_observe_uncommitted_stale_recovery() -> None:
    engine = get_engine()
    job = _processing_job()
    job.priority = 1_000_000
    recovery_flushed = Event()
    release_recovery = Event()

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()

        job_id = job.id
        old_worker_id = job.worker_id
        old_claim_token = job.claim_token

    assert old_worker_id is not None
    assert old_claim_token is not None

    def recover() -> RecoveredBackgroundJobs:
        with Session(engine) as session:
            result = _recovery_service(session).execute(
                RecoverStaleBackgroundJobsCommand(
                    batch_size=1,
                )
            )

            assert result.recovered_for_retry == (job_id,)
            recovery_flushed.set()
            assert release_recovery.wait(timeout=10)
            session.commit()

            return result

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            recovery_future = executor.submit(recover)

            assert recovery_flushed.wait(timeout=10)

            with Session(engine) as claim_session:
                claims_during_recovery = ClaimBackgroundJobsService(
                    BackgroundJobRepository(claim_session)
                ).execute(
                    ClaimBackgroundJobsCommand(
                        worker_id="worker-during-recovery",
                        batch_size=1,
                        lease_duration=timedelta(minutes=5),
                    )
                )
                claim_session.commit()

            assert claims_during_recovery == ()

            release_recovery.set()
            recovery_result = recovery_future.result(timeout=20)

        assert recovery_result.recovered_for_retry == (job_id,)

        with Session(engine) as availability_session:
            recovered = availability_session.get(
                BackgroundJob,
                job_id,
            )

            assert recovered is not None
            assert recovered.status is BackgroundJobStatus.RETRY_SCHEDULED

            recovered.available_at = datetime.now(UTC) - timedelta(seconds=1)
            availability_session.commit()

        with Session(engine) as claim_session:
            claims_after_recovery = ClaimBackgroundJobsService(
                BackgroundJobRepository(claim_session)
            ).execute(
                ClaimBackgroundJobsCommand(
                    worker_id="worker-after-recovery",
                    batch_size=1,
                    lease_duration=timedelta(minutes=5),
                )
            )
            claim_session.commit()

        assert len(claims_after_recovery) == 1

        claimed = claims_after_recovery[0]

        assert claimed.job_id == job_id
        assert claimed.processing_attempt_count == 2
        assert claimed.worker_id == "worker-after-recovery"
        assert claimed.claim_token != old_claim_token

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.PROCESSING
            assert persisted.processing_attempt_count == 2
            assert persisted.worker_id == "worker-after-recovery"
            assert persisted.claim_token != old_claim_token
            assert persisted.worker_id != old_worker_id
    finally:
        release_recovery.set()

        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id == job_id))
            cleanup_session.commit()


def test_concurrent_completion_and_failure_allow_one_terminal_transition() -> None:
    engine = get_engine()
    job = _processing_job()
    barrier = Barrier(2)

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()

        job_id = job.id
        worker_id = job.worker_id
        claim_token = job.claim_token

    assert worker_id is not None
    assert claim_token is not None

    def complete() -> str:
        with Session(engine) as session:
            barrier.wait(timeout=10)

            try:
                CompleteBackgroundJobService(BackgroundJobRepository(session)).execute(
                    CompleteBackgroundJobCommand(
                        job_id=job_id,
                        worker_id=worker_id,
                        claim_token=claim_token,
                    )
                )
                session.commit()

                return "succeeded"
            except BackgroundJobInvalidTransitionError:
                session.rollback()

                return "invalid_transition"

    def fail() -> str:
        with Session(engine) as session:
            barrier.wait(timeout=10)

            try:
                FailBackgroundJobService(
                    BackgroundJobRepository(session),
                    _retry_policy(),
                ).execute(
                    FailBackgroundJobCommand(
                        job_id=job_id,
                        worker_id=worker_id,
                        claim_token=claim_token,
                        failure_kind=BackgroundJobFailureKind.TERMINAL,
                        error_code="terminal_test_failure",
                        error_message="Terminal test failure.",
                    )
                )
                session.commit()

                return "dead_lettered"
            except BackgroundJobInvalidTransitionError:
                session.rollback()

                return "invalid_transition"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            completion_future = executor.submit(complete)
            failure_future = executor.submit(fail)

            results = [
                completion_future.result(timeout=20),
                failure_future.result(timeout=20),
            ]

        assert results.count("invalid_transition") == 1

        successful = next(result for result in results if result != "invalid_transition")

        assert successful in {"succeeded", "dead_lettered"}

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.worker_id is None
            assert persisted.claim_token is None
            assert persisted.claimed_at is None
            assert persisted.lease_expires_at is None

            if successful == "succeeded":
                assert persisted.status is BackgroundJobStatus.SUCCEEDED
                assert persisted.completed_at is not None
                assert persisted.dead_lettered_at is None
            else:
                assert persisted.status is BackgroundJobStatus.DEAD_LETTERED
                assert persisted.dead_lettered_at is not None
                assert persisted.completed_at is None
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id == job_id))
            cleanup_session.commit()
