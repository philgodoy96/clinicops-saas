from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.contracts import EnqueueBackgroundJobCommand
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import BackgroundJobIdempotencyConflictError
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.services.enqueue_background_job import (
    EnqueueBackgroundJobService,
)


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _command(
    *,
    idempotency_key: str | None = None,
    webhook_event_id: str | None = None,
) -> EnqueueBackgroundJobCommand:
    return EnqueueBackgroundJobCommand(
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": (webhook_event_id or str(uuid4())),
        },
        correlation_id=f"correlation-{uuid4()}",
        idempotency_key=idempotency_key,
    )


def _service(
    session: Session,
) -> EnqueueBackgroundJobService:
    return EnqueueBackgroundJobService(BackgroundJobRepository(session))


def test_enqueue_persists_new_background_job(
    db_session: Session,
) -> None:
    result = _service(db_session).execute(_command())

    persisted = db_session.get(BackgroundJob, result.job_id)

    assert result.created is True
    assert persisted is not None
    assert persisted.id == result.job_id
    assert persisted.job_type == "billing.webhook.process"
    assert persisted.payload_version == 1
    assert persisted.available_at is not None


def test_equivalent_idempotent_enqueue_returns_existing_job(
    db_session: Session,
) -> None:
    idempotency_key = f"background-job:{uuid4()}"
    webhook_event_id = str(uuid4())
    service = _service(db_session)

    first = service.execute(
        _command(
            idempotency_key=idempotency_key,
            webhook_event_id=webhook_event_id,
        )
    )
    second = service.execute(
        _command(
            idempotency_key=idempotency_key,
            webhook_event_id=webhook_event_id,
        )
    )

    count_statement = select(func.count(BackgroundJob.id)).where(
        BackgroundJob.idempotency_key == idempotency_key
    )

    assert first.created is True
    assert second.created is False
    assert second.job_id == first.job_id
    assert db_session.scalar(count_statement) == 1


def test_idempotency_key_reuse_for_different_work_conflicts(
    db_session: Session,
) -> None:
    idempotency_key = f"background-job:{uuid4()}"
    service = _service(db_session)

    service.execute(
        _command(
            idempotency_key=idempotency_key,
            webhook_event_id="event-one",
        )
    )

    with pytest.raises(BackgroundJobIdempotencyConflictError):
        service.execute(
            _command(
                idempotency_key=idempotency_key,
                webhook_event_id="event-two",
            )
        )


def test_completed_job_remains_the_idempotent_result(
    db_session: Session,
) -> None:
    idempotency_key = f"background-job:{uuid4()}"
    webhook_event_id = str(uuid4())
    service = _service(db_session)

    first = service.execute(
        _command(
            idempotency_key=idempotency_key,
            webhook_event_id=webhook_event_id,
        )
    )
    persisted = db_session.get(
        BackgroundJob,
        first.job_id,
    )

    assert persisted is not None

    persisted.status = BackgroundJobStatus.SUCCEEDED
    persisted.completed_at = func.now()
    db_session.flush()

    replay = service.execute(
        _command(
            idempotency_key=idempotency_key,
            webhook_event_id=webhook_event_id,
        )
    )

    assert replay.created is False
    assert replay.job_id == first.job_id


def test_enqueue_service_does_not_commit(
    db_session: Session,
) -> None:
    result = _service(db_session).execute(_command())

    with Session(get_engine()) as independent_session:
        independently_visible = independent_session.get(
            BackgroundJob,
            result.job_id,
        )

    assert independently_visible is None
    assert db_session.in_transaction() is True


def test_outer_transaction_rollback_removes_enqueued_job(
    db_session: Session,
) -> None:
    result = _service(db_session).execute(_command())

    db_session.rollback()

    with Session(get_engine()) as independent_session:
        persisted = independent_session.get(
            BackgroundJob,
            result.job_id,
        )

    assert persisted is None


def test_concurrent_idempotent_enqueue_creates_one_job() -> None:
    engine = get_engine()
    idempotency_key = f"background-job:{uuid4()}"
    webhook_event_id = str(uuid4())
    barrier = Barrier(2)

    def enqueue() -> tuple[UUID, bool]:
        with Session(engine) as session:
            service = _service(session)
            barrier.wait(timeout=10)

            result = service.execute(
                _command(
                    idempotency_key=idempotency_key,
                    webhook_event_id=webhook_event_id,
                )
            )
            session.commit()

            return result.job_id, result.created

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(enqueue) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]

        job_ids = {job_id for job_id, _created in results}
        created_values = sorted(created for _job_id, created in results)

        assert len(job_ids) == 1
        assert created_values == [False, True]

        with Session(engine) as verification_session:
            count_statement = select(func.count(BackgroundJob.id)).where(
                BackgroundJob.idempotency_key == idempotency_key
            )

            assert verification_session.scalar(count_statement) == 1
    finally:
        with Session(engine) as cleanup_session:
            cleanup_session.execute(
                delete(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key)
            )
            cleanup_session.commit()
