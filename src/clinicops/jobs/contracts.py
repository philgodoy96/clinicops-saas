from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from clinicops.jobs.enums import BackgroundJobStatus

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


__all__ = [
    "EnqueueBackgroundJobCommand",
    "EnqueuedBackgroundJob",
    "JSONObject",
    "JSONScalar",
    "JSONValue",
]
