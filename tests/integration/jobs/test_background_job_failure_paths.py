from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.contracts import (
    ClaimBackgroundJobsCommand,
    ClaimedBackgroundJob,
    CompleteBackgroundJobCommand,
    FailBackgroundJobCommand,
)
from clinicops.jobs.enums import (
    BackgroundJobFailureKind,
    BackgroundJobStatus,
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

_TEST_JOB_PRIORITY = 2_147_483_647


def _queued_job(
    *,
    max_attempts: int = 5,
) -> BackgroundJob:
    return BackgroundJob(
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(uuid4()),
        },
        status=BackgroundJobStatus.QUEUED,
        idempotency_key=f"failure-path-test:{uuid4()}",
        priority=_TEST_JOB_PRIORITY,
        # Stay invisible until the test opens the claim window on DB time.
        available_at=(datetime.now(UTC) + timedelta(hours=1)),
        processing_attempt_count=0,
        max_attempts=max_attempts,
        correlation_id=f"correlation-{uuid4()}",
    )


def _retry_policy() -> BackgroundJobRetryPolicy:
    return BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
        random_value_provider=lambda: 0.0,
    )


def _clear_competing_claimable_jobs(owned_job_id: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(
            delete(BackgroundJob).where(
                BackgroundJob.id != owned_job_id,
                BackgroundJob.status.in_(
                    (
                        BackgroundJobStatus.QUEUED,
                        BackgroundJobStatus.RETRY_SCHEDULED,
                    )
                ),
                BackgroundJob.available_at <= func.now(),
                (BackgroundJob.processing_attempt_count < BackgroundJob.max_attempts),
            )
        )
        session.commit()


def _open_claim_window(job_id: UUID) -> None:
    with Session(get_engine()) as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        assert job.status in {
            BackgroundJobStatus.QUEUED,
            BackgroundJobStatus.RETRY_SCHEDULED,
        }

        database_now = session.execute(select(func.now())).scalar_one()
        job.priority = _TEST_JOB_PRIORITY
        job.available_at = database_now - timedelta(hours=1)
        session.commit()


def _prepare_owned_job_for_claim(job_id: UUID) -> None:
    _clear_competing_claimable_jobs(job_id)
    _open_claim_window(job_id)


def _claim_one(
    session: Session,
    *,
    worker_id: str,
) -> ClaimedBackgroundJob:
    claims = ClaimBackgroundJobsService(BackgroundJobRepository(session)).execute(
        ClaimBackgroundJobsCommand(
            worker_id=worker_id,
            batch_size=1,
            lease_duration=timedelta(minutes=5),
        )
    )

    assert len(claims) == 1

    return claims[0]


def _make_available_for_retry(
    session: Session,
    *,
    job_id: UUID,
) -> None:
    job = session.get(BackgroundJob, job_id)

    assert job is not None
    assert job.status is BackgroundJobStatus.RETRY_SCHEDULED

    database_now = session.execute(select(func.now())).scalar_one()
    job.available_at = database_now - timedelta(seconds=1)
    session.commit()


def _delete_job(job_id: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(BackgroundJob).where(BackgroundJob.id == job_id))
        session.commit()


def test_retryable_failure_can_later_succeed() -> None:
    engine = get_engine()
    job = _queued_job(max_attempts=3)

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()
        job_id = job.id

    try:
        _prepare_owned_job_for_claim(job_id)

        with Session(engine) as session:
            first_claim = _claim_one(
                session,
                worker_id="worker-first-attempt",
            )
            session.commit()

            first_claim_token = first_claim.claim_token

            first_failure = FailBackgroundJobService(
                BackgroundJobRepository(session),
                _retry_policy(),
            ).execute(
                FailBackgroundJobCommand(
                    job_id=job_id,
                    worker_id="worker-first-attempt",
                    claim_token=first_claim_token,
                    failure_kind=(BackgroundJobFailureKind.RETRYABLE),
                    error_code="provider_unavailable",
                    error_message=("Provider temporarily unavailable."),
                )
            )
            session.commit()

            assert first_failure.status is BackgroundJobStatus.RETRY_SCHEDULED
            assert first_failure.processing_attempt_count == 1

            _make_available_for_retry(
                session,
                job_id=job_id,
            )
            _clear_competing_claimable_jobs(job_id)

            second_claim = _claim_one(
                session,
                worker_id="worker-second-attempt",
            )
            session.commit()

            assert second_claim.job_id == job_id
            assert second_claim.processing_attempt_count == 2
            assert second_claim.claim_token != first_claim_token

            completed = CompleteBackgroundJobService(BackgroundJobRepository(session)).execute(
                CompleteBackgroundJobCommand(
                    job_id=job_id,
                    worker_id="worker-second-attempt",
                    claim_token=second_claim.claim_token,
                )
            )
            session.commit()

            assert completed.status is BackgroundJobStatus.SUCCEEDED
            assert completed.processing_attempt_count == 2

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.SUCCEEDED
            assert persisted.processing_attempt_count == 2
            assert persisted.completed_at is not None
            assert persisted.dead_lettered_at is None
            assert persisted.worker_id is None
            assert persisted.claim_token is None

            assert persisted.last_error_code == "provider_unavailable"
            assert persisted.last_error_message == "Provider temporarily unavailable."
            assert persisted.last_failed_at is not None
    finally:
        _delete_job(job_id)


def test_retryable_failures_dead_letter_at_maximum_attempts() -> None:
    engine = get_engine()
    job = _queued_job(max_attempts=2)

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()
        job_id = job.id

    try:
        _prepare_owned_job_for_claim(job_id)

        with Session(engine) as session:
            first_claim = _claim_one(
                session,
                worker_id="worker-retry-one",
            )
            session.commit()

            first_failure = FailBackgroundJobService(
                BackgroundJobRepository(session),
                _retry_policy(),
            ).execute(
                FailBackgroundJobCommand(
                    job_id=job_id,
                    worker_id="worker-retry-one",
                    claim_token=first_claim.claim_token,
                    failure_kind=(BackgroundJobFailureKind.RETRYABLE),
                    error_code="provider_unavailable",
                    error_message=("Provider temporarily unavailable."),
                )
            )
            session.commit()

            assert first_failure.status is BackgroundJobStatus.RETRY_SCHEDULED
            assert first_failure.processing_attempt_count == 1

            _make_available_for_retry(
                session,
                job_id=job_id,
            )
            _clear_competing_claimable_jobs(job_id)

            second_claim = _claim_one(
                session,
                worker_id="worker-retry-two",
            )
            session.commit()

            assert second_claim.processing_attempt_count == 2

            second_failure = FailBackgroundJobService(
                BackgroundJobRepository(session),
                _retry_policy(),
            ).execute(
                FailBackgroundJobCommand(
                    job_id=job_id,
                    worker_id="worker-retry-two",
                    claim_token=second_claim.claim_token,
                    failure_kind=(BackgroundJobFailureKind.RETRYABLE),
                    error_code="provider_unavailable",
                    error_message=("Provider remains unavailable."),
                )
            )
            session.commit()

            assert second_failure.status is BackgroundJobStatus.DEAD_LETTERED
            assert second_failure.processing_attempt_count == 2
            assert second_failure.max_attempts == 2
            assert second_failure.dead_lettered_at is not None

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.DEAD_LETTERED
            assert persisted.processing_attempt_count == 2
            assert persisted.dead_lettered_at is not None
            assert persisted.completed_at is None
            assert persisted.worker_id is None
            assert persisted.claim_token is None
    finally:
        _delete_job(job_id)


def test_terminal_failure_dead_letters_without_extra_attempts() -> None:
    engine = get_engine()
    job = _queued_job(max_attempts=5)

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()
        job_id = job.id

    try:
        _prepare_owned_job_for_claim(job_id)

        with Session(engine) as session:
            claim = _claim_one(
                session,
                worker_id="worker-terminal-failure",
            )
            session.commit()

            result = FailBackgroundJobService(
                BackgroundJobRepository(session),
                _retry_policy(),
            ).execute(
                FailBackgroundJobCommand(
                    job_id=job_id,
                    worker_id="worker-terminal-failure",
                    claim_token=claim.claim_token,
                    failure_kind=(BackgroundJobFailureKind.TERMINAL),
                    error_code="unsupported_payload_version",
                    error_message=("The job payload version is unsupported."),
                )
            )
            session.commit()

            assert result.status is BackgroundJobStatus.DEAD_LETTERED
            assert result.processing_attempt_count == 1
            assert result.max_attempts == 5
            assert result.dead_lettered_at is not None

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.DEAD_LETTERED
            assert persisted.processing_attempt_count == 1
            assert persisted.last_error_code == "unsupported_payload_version"
            assert persisted.dead_lettered_at is not None
            assert persisted.completed_at is None
    finally:
        _delete_job(job_id)


def test_failure_transition_rollback_preserves_active_claim() -> None:
    engine = get_engine()
    job = _queued_job(max_attempts=5)

    with Session(engine) as seed_session:
        seed_session.add(job)
        seed_session.commit()
        job_id = job.id

    try:
        _prepare_owned_job_for_claim(job_id)

        with Session(engine) as claim_session:
            claim = _claim_one(
                claim_session,
                worker_id="worker-rollback",
            )
            claim_session.commit()

        original_claim_token = claim.claim_token
        original_claimed_at = claim.claimed_at
        original_lease_expires_at = claim.lease_expires_at

        with Session(engine) as failure_session:
            result = FailBackgroundJobService(
                BackgroundJobRepository(failure_session),
                _retry_policy(),
            ).execute(
                FailBackgroundJobCommand(
                    job_id=job_id,
                    worker_id="worker-rollback",
                    claim_token=original_claim_token,
                    failure_kind=(BackgroundJobFailureKind.RETRYABLE),
                    error_code="temporary_failure",
                    error_message="Temporary failure.",
                )
            )

            assert result.status is BackgroundJobStatus.RETRY_SCHEDULED

            failure_session.rollback()

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                BackgroundJob,
                job_id,
            )

            assert persisted is not None
            assert persisted.status is BackgroundJobStatus.PROCESSING
            assert persisted.processing_attempt_count == 1
            assert persisted.worker_id == "worker-rollback"
            assert persisted.claim_token == original_claim_token
            assert persisted.claimed_at == original_claimed_at
            assert persisted.lease_expires_at == original_lease_expires_at
            assert persisted.last_error_code is None
            assert persisted.last_error_message is None
            assert persisted.last_failed_at is None
            assert persisted.dead_lettered_at is None
    finally:
        _delete_job(job_id)
