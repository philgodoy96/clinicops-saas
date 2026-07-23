from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.contracts import (
    ClaimBackgroundJobsCommand,
    ClaimedBackgroundJob,
)
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.services.claim_background_jobs import (
    ClaimBackgroundJobsService,
)

_TEST_JOB_PRIORITY = 2_147_483_647


def _clear_competing_claimable_jobs(*owned_job_ids: UUID) -> None:
    owned_ids = set(owned_job_ids)

    with Session(get_engine()) as session:
        statement = delete(BackgroundJob).where(
            BackgroundJob.status.in_(
                (
                    BackgroundJobStatus.QUEUED,
                    BackgroundJobStatus.RETRY_SCHEDULED,
                )
            ),
            BackgroundJob.available_at <= func.now(),
            (BackgroundJob.processing_attempt_count < BackgroundJob.max_attempts),
        )

        if owned_ids:
            statement = statement.where(BackgroundJob.id.not_in(owned_ids))

        session.execute(statement)
        session.commit()


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _new_job(
    *,
    priority: int = 0,
    available_at: datetime | None = None,
    status: BackgroundJobStatus = (BackgroundJobStatus.QUEUED),
    processing_attempt_count: int = 0,
    max_attempts: int = 5,
) -> BackgroundJob:
    return BackgroundJob(
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(uuid4()),
        },
        status=status,
        idempotency_key=f"claim-test:{uuid4()}",
        priority=priority,
        available_at=(available_at or datetime.now(UTC) - timedelta(minutes=1)),
        processing_attempt_count=processing_attempt_count,
        max_attempts=max_attempts,
        correlation_id=f"correlation-{uuid4()}",
    )


def _owned_claim_job(
    *,
    status: BackgroundJobStatus = BackgroundJobStatus.QUEUED,
    processing_attempt_count: int = 0,
    max_attempts: int = 5,
) -> BackgroundJob:
    return _new_job(
        priority=_TEST_JOB_PRIORITY,
        # Stay invisible until the test opens the claim window on DB time.
        available_at=(datetime.now(UTC) + timedelta(hours=1)),
        status=status,
        processing_attempt_count=processing_attempt_count,
        max_attempts=max_attempts,
    )


def _open_claim_window(
    session: Session,
    job_id: UUID,
) -> None:
    job = session.get(BackgroundJob, job_id)
    assert job is not None
    assert job.status in {
        BackgroundJobStatus.QUEUED,
        BackgroundJobStatus.RETRY_SCHEDULED,
    }

    database_now = session.execute(select(func.now())).scalar_one()
    job.priority = _TEST_JOB_PRIORITY
    job.available_at = database_now - timedelta(hours=1)
    session.flush()


def _service(
    session: Session,
) -> ClaimBackgroundJobsService:
    return ClaimBackgroundJobsService(BackgroundJobRepository(session))


def _claim_command(
    *,
    worker_id: str,
    batch_size: int = 10,
) -> ClaimBackgroundJobsCommand:
    return ClaimBackgroundJobsCommand(
        worker_id=worker_id,
        batch_size=batch_size,
        lease_duration=timedelta(minutes=5),
    )


def _claim_owned_job(
    session: Session,
    *,
    job_id: UUID,
    worker_id: str,
) -> ClaimedBackgroundJob:
    _clear_competing_claimable_jobs(job_id)
    _open_claim_window(session, job_id)
    claims = _service(session).execute(
        _claim_command(
            worker_id=worker_id,
            batch_size=1,
        )
    )
    assert len(claims) == 1
    assert claims[0].job_id == job_id
    return claims[0]


def test_claims_eligible_jobs_in_expected_order(
    db_session: Session,
) -> None:
    _clear_competing_claimable_jobs()

    now = datetime.now(UTC)

    lowest_priority = _new_job(
        priority=0,
        available_at=now - timedelta(minutes=10),
    )
    later_high_priority = _new_job(
        priority=10,
        available_at=now - timedelta(minutes=5),
    )
    earlier_high_priority = _new_job(
        priority=10,
        available_at=now - timedelta(minutes=10),
        status=BackgroundJobStatus.RETRY_SCHEDULED,
        processing_attempt_count=1,
    )
    future_job = _new_job(
        priority=100,
        available_at=now + timedelta(hours=1),
    )
    exhausted_job = _new_job(
        priority=100,
        processing_attempt_count=5,
        max_attempts=5,
    )

    db_session.add_all(
        [
            lowest_priority,
            later_high_priority,
            earlier_high_priority,
            future_job,
            exhausted_job,
        ]
    )
    db_session.flush()

    claims = _service(db_session).execute(
        _claim_command(
            worker_id="worker-ordering",
            batch_size=3,
        )
    )

    assert [claim.job_id for claim in claims] == [
        earlier_high_priority.id,
        later_high_priority.id,
        lowest_priority.id,
    ]

    assert future_job.status is BackgroundJobStatus.QUEUED
    assert exhausted_job.status is BackgroundJobStatus.QUEUED


def test_claim_sets_attempt_and_ownership_fields(
    db_session: Session,
) -> None:
    job = _owned_claim_job(
        status=BackgroundJobStatus.RETRY_SCHEDULED,
        processing_attempt_count=2,
    )

    db_session.add(job)
    db_session.flush()

    claim = _claim_owned_job(
        db_session,
        job_id=job.id,
        worker_id="worker-ownership",
    )

    assert job.status is BackgroundJobStatus.PROCESSING
    assert job.processing_attempt_count == 3
    assert job.worker_id == "worker-ownership"
    assert job.claim_token == claim.claim_token
    assert job.claimed_at == claim.claimed_at
    assert job.lease_expires_at == claim.lease_expires_at
    assert job.lease_expires_at > job.claimed_at


def test_claim_service_does_not_commit() -> None:
    engine = get_engine()
    job = _owned_claim_job()

    try:
        with Session(engine) as seed_session:
            seed_session.add(job)
            seed_session.commit()
            job_id = job.id

        with Session(engine) as claim_session:
            _claim_owned_job(
                claim_session,
                job_id=job_id,
                worker_id="worker-no-commit",
            )

            with Session(engine) as independent_session:
                persisted = independent_session.get(
                    BackgroundJob,
                    job_id,
                )

                assert persisted is not None
                assert persisted.status is BackgroundJobStatus.QUEUED
                assert persisted.worker_id is None

            claim_session.rollback()
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id == job.id))
            cleanup_session.commit()


def test_claim_rollback_leaves_job_eligible() -> None:
    engine = get_engine()
    job = _owned_claim_job()

    try:
        with Session(engine) as seed_session:
            seed_session.add(job)
            seed_session.commit()
            job_id = job.id

        with Session(engine) as first_session:
            _claim_owned_job(
                first_session,
                job_id=job_id,
                worker_id="worker-first",
            )
            first_session.rollback()

        with Session(engine) as second_session:
            second_claim = _claim_owned_job(
                second_session,
                job_id=job_id,
                worker_id="worker-second",
            )

            assert second_claim.worker_id == "worker-second"
            second_session.rollback()
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id == job.id))
            cleanup_session.commit()


def test_concurrent_workers_do_not_claim_same_jobs() -> None:
    engine = get_engine()
    jobs = [_owned_claim_job() for _ in range(6)]
    barrier = Barrier(2)

    with Session(engine) as seed_session:
        seed_session.add_all(jobs)
        seed_session.commit()
        job_ids = {job.id for job in jobs}

    _clear_competing_claimable_jobs(*job_ids)

    with Session(engine) as readiness_session:
        for job_id in job_ids:
            _open_claim_window(readiness_session, job_id)
        readiness_session.commit()

    def claim(worker_id: str) -> tuple[UUID, ...]:
        with Session(engine) as session:
            barrier.wait(timeout=10)

            claims = _service(session).execute(
                _claim_command(
                    worker_id=worker_id,
                    batch_size=3,
                )
            )
            session.commit()

            return tuple(claim.job_id for claim in claims)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                claim,
                "worker-concurrent-1",
            )
            second_future = executor.submit(
                claim,
                "worker-concurrent-2",
            )

            first_claims = first_future.result(timeout=20)
            second_claims = second_future.result(timeout=20)

        assert len(first_claims) == 3
        assert len(second_claims) == 3
        assert set(first_claims).isdisjoint(second_claims)
        assert set(first_claims) | set(second_claims) == job_ids

        with Session(engine) as verification_session:
            persisted_jobs = (
                verification_session.query(BackgroundJob)
                .filter(BackgroundJob.id.in_(job_ids))
                .all()
            )

            assert len(persisted_jobs) == 6
            assert all(job.status is BackgroundJobStatus.PROCESSING for job in persisted_jobs)
            assert {job.worker_id for job in persisted_jobs} == {
                "worker-concurrent-1",
                "worker-concurrent-2",
            }
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id.in_(job_ids)))
            cleanup_session.commit()
