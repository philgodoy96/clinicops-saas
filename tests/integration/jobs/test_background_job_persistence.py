from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.models import BackgroundJob


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _new_background_job(
    *,
    idempotency_key: str | None = None,
    status: BackgroundJobStatus | None = None,
) -> BackgroundJob:
    webhook_event_id = uuid4()

    values: dict[str, object] = {
        "job_type": "billing.webhook.process",
        "payload_version": 1,
        "payload": {
            "webhook_event_id": str(webhook_event_id),
        },
        "idempotency_key": idempotency_key,
        "correlation_id": f"correlation-{uuid4()}",
    }

    if status is not None:
        values["status"] = status

    return BackgroundJob(**values)


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def test_background_job_persists_database_defaults(
    db_session: Session,
) -> None:
    job = _new_background_job()

    db_session.add(job)
    db_session.flush()
    db_session.refresh(job)

    assert job.id is not None
    assert job.status is BackgroundJobStatus.QUEUED
    assert job.priority == 0
    assert job.available_at is not None
    assert job.processing_attempt_count == 0
    assert job.max_attempts == 5
    assert job.worker_id is None
    assert job.claim_token is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None
    assert job.completed_at is None
    assert job.dead_lettered_at is None
    assert job.created_at is not None
    assert job.updated_at is not None


def test_background_job_persists_jsonb_payload(
    db_session: Session,
) -> None:
    webhook_event_id = uuid4()

    job = BackgroundJob(
        job_type="billing.webhook.process",
        payload_version=1,
        payload={
            "webhook_event_id": str(webhook_event_id),
            "metadata": {
                "source": "billing_webhook",
                "replayed": False,
            },
        },
        correlation_id=f"correlation-{uuid4()}",
    )

    db_session.add(job)
    db_session.flush()
    db_session.expire(job)
    db_session.refresh(job)

    assert job.payload == {
        "webhook_event_id": str(webhook_event_id),
        "metadata": {
            "source": "billing_webhook",
            "replayed": False,
        },
    }


def test_background_job_idempotency_key_is_unique_when_present(
    db_session: Session,
) -> None:
    idempotency_key = f"background-job:{uuid4()}"

    first = _new_background_job(
        idempotency_key=idempotency_key,
    )
    second = _new_background_job(
        idempotency_key=idempotency_key,
    )

    db_session.add(first)
    db_session.flush()

    db_session.add(second)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert _constraint_name(exception_info.value) == "uq_background_jobs_idempotency_key"


def test_background_jobs_allow_multiple_null_idempotency_keys(
    db_session: Session,
) -> None:
    first = _new_background_job()
    second = _new_background_job()

    db_session.add_all([first, second])
    db_session.flush()

    assert first.id is not None
    assert second.id is not None
    assert first.id != second.id


def test_processing_job_requires_claim_ownership(
    db_session: Session,
) -> None:
    job = _new_background_job(
        status=BackgroundJobStatus.PROCESSING,
    )

    db_session.add(job)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert _constraint_name(exception_info.value) == "ck_background_jobs_lifecycle_consistency"


def test_background_job_table_has_worker_polling_indexes(
    db_session: Session,
) -> None:
    inspector = inspect(db_session.connection())

    index_names = {index["name"] for index in inspector.get_indexes("background_jobs")}

    assert {
        "uq_background_jobs_idempotency_key",
        "ix_background_jobs_available",
        "ix_background_jobs_stale_processing",
    }.issubset(index_names)
