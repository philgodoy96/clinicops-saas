from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from clinicops.jobs.enums import BackgroundJobFailureKind, BackgroundJobStatus

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class EnqueueBackgroundJobCommand:
    job_type: str
    payload_version: int
    payload: JSONObject
    correlation_id: str
    idempotency_key: str | None = None
    priority: int = 0
    available_at: datetime | None = None
    max_attempts: int = 5
    origin_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class EnqueuedBackgroundJob:
    job_id: UUID
    job_type: str
    status: BackgroundJobStatus
    created: bool
    available_at: datetime
    processing_attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class ClaimBackgroundJobsCommand:
    worker_id: str
    batch_size: int
    lease_duration: timedelta


@dataclass(frozen=True, slots=True)
class ClaimedBackgroundJob:
    job_id: UUID
    job_type: str
    payload_version: int
    payload: JSONObject
    processing_attempt_count: int
    max_attempts: int
    worker_id: str
    claim_token: UUID
    claimed_at: datetime
    lease_expires_at: datetime
    correlation_id: str
    origin_request_id: str | None


@dataclass(frozen=True, slots=True)
class CompleteBackgroundJobCommand:
    job_id: UUID
    worker_id: str
    claim_token: UUID


@dataclass(frozen=True, slots=True)
class CompletedBackgroundJob:
    job_id: UUID
    status: BackgroundJobStatus
    completed_at: datetime
    processing_attempt_count: int


@dataclass(frozen=True, slots=True)
class FailBackgroundJobCommand:
    job_id: UUID
    worker_id: str
    claim_token: UUID
    failure_kind: BackgroundJobFailureKind
    error_code: str
    error_message: str


@dataclass(frozen=True, slots=True)
class FailedBackgroundJob:
    job_id: UUID
    status: BackgroundJobStatus
    processing_attempt_count: int
    max_attempts: int
    available_at: datetime
    last_failed_at: datetime
    dead_lettered_at: datetime | None


@dataclass(frozen=True, slots=True)
class RecoverStaleBackgroundJobsCommand:
    batch_size: int


@dataclass(frozen=True, slots=True)
class RecoveredBackgroundJobs:
    recovered_for_retry: tuple[UUID, ...]
    dead_lettered: tuple[UUID, ...]


__all__ = [
    "ClaimBackgroundJobsCommand",
    "ClaimedBackgroundJob",
    "CompleteBackgroundJobCommand",
    "CompletedBackgroundJob",
    "EnqueueBackgroundJobCommand",
    "EnqueuedBackgroundJob",
    "FailBackgroundJobCommand",
    "FailedBackgroundJob",
    "JSONObject",
    "JSONScalar",
    "JSONValue",
    "RecoverStaleBackgroundJobsCommand",
    "RecoveredBackgroundJobs",
]
