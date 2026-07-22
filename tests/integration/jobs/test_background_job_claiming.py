from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.contracts import ClaimBackgroundJobsCommand
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.services.claim_background_jobs import (
    ClaimBackgroundJobsService,
)


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


def test_claims_eligible_jobs_in_expected_order(
    db_session: Session,
) -> None:
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
    job = _new_job(
        status=BackgroundJobStatus.RETRY_SCHEDULED,
        processing_attempt_count=2,
    )

    db_session.add(job)
    db_session.flush()

    claims = _service(db_session).execute(
        _claim_command(
            worker_id="worker-ownership",
            batch_size=1,
        )
    )

    assert len(claims) == 1

    claim = claims[0]

    assert job.status is BackgroundJobStatus.PROCESSING
    assert job.processing_attempt_count == 3
    assert job.worker_id == "worker-ownership"
    assert job.claim_token == claim.claim_token
    assert job.claimed_at == claim.claimed_at
    assert job.lease_expires_at == claim.lease_expires_at
    assert job.lease_expires_at > job.claimed_at


def test_claim_service_does_not_commit() -> None:
    engine = get_engine()
    job = _new_job()

    try:
        with Session(engine) as seed_session:
            seed_session.add(job)
            seed_session.commit()
            job_id = job.id

        with Session(engine) as claim_session:
            claims = _service(claim_session).execute(
                _claim_command(
                    worker_id="worker-no-commit",
                    batch_size=1,
                )
            )

            assert claims[0].job_id == job_id

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
    job = _new_job()

    try:
        with Session(engine) as seed_session:
            seed_session.add(job)
            seed_session.commit()
            job_id = job.id

        with Session(engine) as first_session:
            first_claims = _service(first_session).execute(
                _claim_command(
                    worker_id="worker-first",
                    batch_size=1,
                )
            )

            assert first_claims[0].job_id == job_id
            first_session.rollback()

        with Session(engine) as second_session:
            second_claims = _service(second_session).execute(
                _claim_command(
                    worker_id="worker-second",
                    batch_size=1,
                )
            )

            assert second_claims[0].job_id == job_id
            assert second_claims[0].worker_id == "worker-second"
            second_session.rollback()
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(delete(BackgroundJob).where(BackgroundJob.id == job.id))
            cleanup_session.commit()


def test_concurrent_workers_do_not_claim_same_jobs() -> None:
    engine = get_engine()
    jobs = [_new_job() for _ in range(6)]
    barrier = Barrier(2)

    with Session(engine) as seed_session:
        seed_session.add_all(jobs)
        seed_session.commit()
        job_ids = {job.id for job in jobs}

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
