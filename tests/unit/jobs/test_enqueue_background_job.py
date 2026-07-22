from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from clinicops.jobs.contracts import (
    EnqueueBackgroundJobCommand,
    JSONObject,
)
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import (
    BackgroundJobIdempotencyConflictError,
    BackgroundJobInvalidConfigurationError,
    BackgroundJobInvalidPayloadError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.services.enqueue_background_job import (
    EnqueueBackgroundJobService,
)


class RecordingBackgroundJobRepository:
    def __init__(
        self,
        *,
        existing: BackgroundJob | None = None,
        idempotent_created: bool = True,
    ) -> None:
        self.existing = existing
        self.idempotent_created = idempotent_created
        self.added_job: BackgroundJob | None = None
        self.idempotent_candidate: BackgroundJob | None = None

    def add_and_flush(
        self,
        job: BackgroundJob,
    ) -> BackgroundJob:
        self.added_job = job
        _apply_persistence_defaults(job)
        return job

    def insert_idempotent_or_get_existing(
        self,
        job: BackgroundJob,
    ) -> tuple[BackgroundJob, bool]:
        self.idempotent_candidate = job

        if self.existing is not None:
            return self.existing, self.idempotent_created

        _apply_persistence_defaults(job)
        return job, self.idempotent_created


def _apply_persistence_defaults(
    job: BackgroundJob,
) -> None:
    if job.available_at is None:
        job.available_at = datetime.now(UTC)


def _existing_job(
    *,
    job_type: str = "billing.webhook.process",
    payload_version: int = 1,
    payload: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> BackgroundJob:
    return BackgroundJob(
        id=uuid4(),
        job_type=job_type,
        payload_version=payload_version,
        payload=payload
        or {
            "webhook_event_id": str(uuid4()),
        },
        status=BackgroundJobStatus.QUEUED,
        idempotency_key=idempotency_key,
        priority=0,
        available_at=datetime.now(UTC),
        processing_attempt_count=0,
        max_attempts=5,
        correlation_id=f"correlation-{uuid4()}",
    )


def _command(
    *,
    payload: JSONObject | None = None,
    idempotency_key: str | None = None,
) -> EnqueueBackgroundJobCommand:
    return EnqueueBackgroundJobCommand(
        job_type="billing.webhook.process",
        payload_version=1,
        payload=payload
        or {
            "webhook_event_id": str(uuid4()),
        },
        correlation_id=f"correlation-{uuid4()}",
        idempotency_key=idempotency_key,
    )


def test_enqueue_without_idempotency_key_creates_job() -> None:
    repository = RecordingBackgroundJobRepository()
    service = EnqueueBackgroundJobService(cast(BackgroundJobRepository, repository))

    result = service.execute(_command())

    assert result.created is True
    assert result.status is BackgroundJobStatus.QUEUED
    assert result.processing_attempt_count == 0
    assert repository.added_job is not None
    assert repository.idempotent_candidate is None


def test_enqueue_normalizes_bounded_string_fields() -> None:
    repository = RecordingBackgroundJobRepository()
    service = EnqueueBackgroundJobService(cast(BackgroundJobRepository, repository))

    command = EnqueueBackgroundJobCommand(
        job_type="  billing.webhook.process  ",
        payload_version=1,
        payload={},
        correlation_id="  correlation-123  ",
        idempotency_key="  job-key-123  ",
        origin_request_id="  request-123  ",
    )

    service.execute(command)

    candidate = repository.idempotent_candidate

    assert candidate is not None
    assert candidate.job_type == "billing.webhook.process"
    assert candidate.correlation_id == "correlation-123"
    assert candidate.idempotency_key == "job-key-123"
    assert candidate.origin_request_id == "request-123"


def test_equivalent_idempotent_replay_returns_existing_job() -> None:
    idempotency_key = f"job:{uuid4()}"
    payload: JSONObject = {
        "webhook_event_id": str(uuid4()),
    }
    existing = _existing_job(
        payload=cast(dict[str, object], payload),
        idempotency_key=idempotency_key,
    )
    repository = RecordingBackgroundJobRepository(
        existing=existing,
        idempotent_created=False,
    )
    service = EnqueueBackgroundJobService(cast(BackgroundJobRepository, repository))

    result = service.execute(
        _command(
            payload=payload,
            idempotency_key=idempotency_key,
        )
    )

    assert result.created is False
    assert result.job_id == existing.id


def test_idempotent_replay_allows_operational_metadata_change() -> None:
    idempotency_key = f"job:{uuid4()}"
    payload: JSONObject = {
        "webhook_event_id": str(uuid4()),
    }
    existing = _existing_job(
        payload=cast(dict[str, object], payload),
        idempotency_key=idempotency_key,
    )
    repository = RecordingBackgroundJobRepository(
        existing=existing,
        idempotent_created=False,
    )
    service = EnqueueBackgroundJobService(cast(BackgroundJobRepository, repository))

    command = EnqueueBackgroundJobCommand(
        job_type="billing.webhook.process",
        payload_version=1,
        payload=payload,
        correlation_id=f"new-correlation-{uuid4()}",
        idempotency_key=idempotency_key,
        priority=100,
        max_attempts=10,
    )

    result = service.execute(command)

    assert result.created is False
    assert result.job_id == existing.id
    assert result.max_attempts == existing.max_attempts


@pytest.mark.parametrize(
    ("job_type", "payload_version", "payload"),
    [
        (
            "billing.subscription.reconcile",
            1,
            {"webhook_event_id": "event-123"},
        ),
        (
            "billing.webhook.process",
            2,
            {"webhook_event_id": "event-123"},
        ),
        (
            "billing.webhook.process",
            1,
            {"webhook_event_id": "different-event"},
        ),
    ],
)
def test_idempotency_key_reuse_with_different_work_conflicts(
    job_type: str,
    payload_version: int,
    payload: JSONObject,
) -> None:
    idempotency_key = f"job:{uuid4()}"
    existing = _existing_job(
        payload={
            "webhook_event_id": "event-123",
        },
        idempotency_key=idempotency_key,
    )
    repository = RecordingBackgroundJobRepository(
        existing=existing,
        idempotent_created=False,
    )
    service = EnqueueBackgroundJobService(cast(BackgroundJobRepository, repository))

    command = EnqueueBackgroundJobCommand(
        job_type=job_type,
        payload_version=payload_version,
        payload=payload,
        correlation_id=f"correlation-{uuid4()}",
        idempotency_key=idempotency_key,
    )

    with pytest.raises(BackgroundJobIdempotencyConflictError):
        service.execute(command)


def test_enqueue_rejects_non_finite_json_number() -> None:
    repository = RecordingBackgroundJobRepository()
    service = EnqueueBackgroundJobService(cast(BackgroundJobRepository, repository))

    command = _command(
        payload={
            "invalid_number": float("nan"),
        }
    )

    with pytest.raises(BackgroundJobInvalidPayloadError):
        service.execute(command)


def test_enqueue_rejects_non_json_payload_value() -> None:
    repository = RecordingBackgroundJobRepository()
    service = EnqueueBackgroundJobService(cast(BackgroundJobRepository, repository))
    payload = cast(
        JSONObject,
        {
            "webhook_event_id": uuid4(),
        },
    )

    with pytest.raises(BackgroundJobInvalidPayloadError):
        service.execute(_command(payload=payload))


@pytest.mark.parametrize(
    "command",
    [
        EnqueueBackgroundJobCommand(
            job_type=" ",
            payload_version=1,
            payload={},
            correlation_id="correlation-123",
        ),
        EnqueueBackgroundJobCommand(
            job_type="billing.webhook.process",
            payload_version=0,
            payload={},
            correlation_id="correlation-123",
        ),
        EnqueueBackgroundJobCommand(
            job_type="billing.webhook.process",
            payload_version=1,
            payload={},
            correlation_id=" ",
        ),
        EnqueueBackgroundJobCommand(
            job_type="billing.webhook.process",
            payload_version=1,
            payload={},
            correlation_id="correlation-123",
            max_attempts=0,
        ),
        EnqueueBackgroundJobCommand(
            job_type="billing.webhook.process",
            payload_version=1,
            payload={},
            correlation_id="correlation-123",
            available_at=datetime(2026, 7, 22, 12, 0),
        ),
    ],
)
def test_enqueue_rejects_invalid_configuration(
    command: EnqueueBackgroundJobCommand,
) -> None:
    repository = RecordingBackgroundJobRepository()
    service = EnqueueBackgroundJobService(cast(BackgroundJobRepository, repository))

    with pytest.raises(BackgroundJobInvalidConfigurationError):
        service.execute(command)
