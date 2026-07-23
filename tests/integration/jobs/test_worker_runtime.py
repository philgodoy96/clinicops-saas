from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.contracts import ClaimedBackgroundJob
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.runtime.exceptions import (
    RetryableJobExecutionError,
)
from clinicops.jobs.runtime.registry import JobHandlerRegistry
from clinicops.jobs.runtime.worker import (
    BackgroundWorker,
    SqlAlchemyBackgroundJobRuntimeStore,
    WorkerIterationOutcome,
)

_TEST_JOB_PRIORITY = 2_147_483_647


@dataclass
class DatabaseVisibilityHandler:
    engine: Engine
    job_type: str = "test.worker.success"
    supported_payload_version: int = 1
    observed_processing_state: bool = False

    def execute(
        self,
        job: ClaimedBackgroundJob,
    ) -> None:
        with Session(self.engine) as session:
            persisted = session.get(
                BackgroundJob,
                job.job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.PROCESSING
            assert persisted.worker_id == job.worker_id
            assert persisted.claim_token == job.claim_token

            self.observed_processing_state = True


@dataclass
class RetryableFailureHandler:
    job_type: str = "test.worker.retryable"
    supported_payload_version: int = 1

    def execute(
        self,
        job: ClaimedBackgroundJob,
    ) -> None:
        raise RetryableJobExecutionError(
            "A temporary dependency is unavailable.",
            error_code="temporary_dependency_failure",
        )


def _queued_job(
    *,
    job_type: str,
    payload_version: int = 1,
    max_attempts: int = 5,
) -> BackgroundJob:
    return BackgroundJob(
        job_type=job_type,
        payload_version=payload_version,
        payload={
            "resource_id": str(uuid4()),
        },
        status=BackgroundJobStatus.QUEUED,
        idempotency_key=f"worker-runtime:{uuid4()}",
        priority=_TEST_JOB_PRIORITY,
        available_at=(datetime.now(UTC) - timedelta(minutes=1)),
        processing_attempt_count=0,
        max_attempts=max_attempts,
        correlation_id=f"correlation-{uuid4()}",
    )


def _stale_processing_job() -> BackgroundJob:
    claimed_at = datetime.now(UTC) - timedelta(minutes=10)

    return BackgroundJob(
        job_type="test.worker.stale",
        payload_version=1,
        payload={
            "resource_id": str(uuid4()),
        },
        status=BackgroundJobStatus.PROCESSING,
        idempotency_key=f"worker-runtime:{uuid4()}",
        priority=_TEST_JOB_PRIORITY,
        available_at=claimed_at,
        processing_attempt_count=1,
        max_attempts=5,
        worker_id="worker-that-crashed",
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


def _worker(
    *,
    engine: Engine,
    registry: JobHandlerRegistry,
) -> BackgroundWorker:
    return BackgroundWorker(
        store=SqlAlchemyBackgroundJobRuntimeStore(
            session_factory=lambda: Session(engine),
            retry_policy=_retry_policy(),
        ),
        registry=registry,
        worker_id="integration-worker",
        poll_interval=timedelta(seconds=1),
        lease_duration=timedelta(minutes=5),
        stale_recovery_interval=timedelta(minutes=1),
        stale_recovery_batch_size=50,
    )


def _delete_job(job_id: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(BackgroundJob).where(BackgroundJob.id == job_id))
        session.commit()


def test_worker_commits_claim_before_handler_execution() -> None:
    engine = get_engine()
    job = _queued_job(job_type="test.worker.success")
    handler = DatabaseVisibilityHandler(engine=engine)

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()
        job_id = job.id

    try:
        result = _worker(
            engine=engine,
            registry=JobHandlerRegistry([handler]),
        ).run_once()

        assert result.outcome is WorkerIterationOutcome.SUCCEEDED
        assert result.job_id == job_id
        assert handler.observed_processing_state is True

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.SUCCEEDED
            assert persisted.processing_attempt_count == 1
            assert persisted.completed_at is not None
            assert persisted.worker_id is None
            assert persisted.claim_token is None
    finally:
        _delete_job(job_id)


def test_worker_records_retryable_handler_failure() -> None:
    engine = get_engine()
    job = _queued_job(job_type="test.worker.retryable")

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()
        job_id = job.id

    try:
        result = _worker(
            engine=engine,
            registry=JobHandlerRegistry([RetryableFailureHandler()]),
        ).run_once()

        assert result.outcome is WorkerIterationOutcome.RETRY_SCHEDULED

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.RETRY_SCHEDULED
            assert persisted.processing_attempt_count == 1
            assert persisted.last_error_code == "temporary_dependency_failure"
            assert persisted.last_failed_at is not None
            assert persisted.available_at > persisted.last_failed_at
            assert persisted.worker_id is None
            assert persisted.claim_token is None
    finally:
        _delete_job(job_id)


def test_worker_dead_letters_unknown_job_type() -> None:
    engine = get_engine()
    job = _queued_job(job_type="test.worker.unknown")

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()
        job_id = job.id

    try:
        result = _worker(
            engine=engine,
            registry=JobHandlerRegistry(),
        ).run_once()

        assert result.outcome is WorkerIterationOutcome.DEAD_LETTERED

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.DEAD_LETTERED
            assert persisted.last_error_code == "unknown_job_type"
            assert persisted.dead_lettered_at is not None
            assert persisted.worker_id is None
            assert persisted.claim_token is None
    finally:
        _delete_job(job_id)


def test_worker_recovers_expired_processing_job() -> None:
    engine = get_engine()
    job = _stale_processing_job()

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()
        job_id = job.id
        previous_claim_token = job.claim_token

    try:
        result = _worker(
            engine=engine,
            registry=JobHandlerRegistry(),
        ).run_once()

        assert result.outcome is WorkerIterationOutcome.IDLE

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.RETRY_SCHEDULED
            assert persisted.last_error_code == "processing_lease_expired"
            assert persisted.processing_attempt_count == 1
            assert persisted.worker_id is None
            assert persisted.claim_token is None
            assert persisted.claim_token != previous_claim_token
            assert persisted.lease_expires_at is None
    finally:
        _delete_job(job_id)
