import json
from datetime import datetime
from typing import cast
from uuid import uuid4

from clinicops.jobs.contracts import (
    EnqueueBackgroundJobCommand,
    EnqueuedBackgroundJob,
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

_MAX_INTEGER = 2_147_483_647


class EnqueueBackgroundJobService:
    def __init__(
        self,
        repository: BackgroundJobRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: EnqueueBackgroundJobCommand,
    ) -> EnqueuedBackgroundJob:
        job_type = _normalize_required_string(
            command.job_type,
            field_name="job_type",
            maximum_length=100,
        )
        payload_version = _normalize_positive_integer(
            command.payload_version,
            field_name="payload_version",
        )
        payload = _normalize_payload(command.payload)
        correlation_id = _normalize_required_string(
            command.correlation_id,
            field_name="correlation_id",
            maximum_length=255,
        )
        idempotency_key = _normalize_optional_string(
            command.idempotency_key,
            field_name="idempotency_key",
            maximum_length=255,
        )
        priority = _normalize_integer(
            command.priority,
            field_name="priority",
        )
        max_attempts = _normalize_positive_integer(
            command.max_attempts,
            field_name="max_attempts",
        )
        available_at = _normalize_available_at(command.available_at)
        origin_request_id = _normalize_optional_string(
            command.origin_request_id,
            field_name="origin_request_id",
            maximum_length=255,
        )

        values: dict[str, object] = {
            "id": uuid4(),
            "job_type": job_type,
            "payload_version": payload_version,
            "payload": payload,
            "status": BackgroundJobStatus.QUEUED,
            "idempotency_key": idempotency_key,
            "priority": priority,
            "processing_attempt_count": 0,
            "max_attempts": max_attempts,
            "correlation_id": correlation_id,
            "origin_request_id": origin_request_id,
        }

        if available_at is not None:
            values["available_at"] = available_at

        candidate = BackgroundJob(**values)

        if idempotency_key is None:
            persisted = self._repository.add_and_flush(candidate)
            created = True
        else:
            persisted, created = self._repository.insert_idempotent_or_get_existing(candidate)

            if not created and not _is_semantically_equivalent(
                persisted,
                job_type=job_type,
                payload_version=payload_version,
                payload=payload,
            ):
                raise BackgroundJobIdempotencyConflictError(idempotency_key)

        return EnqueuedBackgroundJob(
            job_id=persisted.id,
            job_type=persisted.job_type,
            status=persisted.status,
            created=created,
            available_at=persisted.available_at,
            processing_attempt_count=(persisted.processing_attempt_count),
            max_attempts=persisted.max_attempts,
        )


def _normalize_payload(
    payload: JSONObject,
) -> JSONObject:
    if not isinstance(payload, dict):
        raise BackgroundJobInvalidPayloadError("Background job payload must be a JSON object.")

    if any(not isinstance(key, str) for key in payload):
        raise BackgroundJobInvalidPayloadError("Background job payload keys must be strings.")

    try:
        serialized = json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        normalized = json.loads(serialized)
    except (TypeError, ValueError) as error:
        raise BackgroundJobInvalidPayloadError(
            "Background job payload must contain only valid JSON values."
        ) from error

    if not isinstance(normalized, dict):
        raise BackgroundJobInvalidPayloadError("Background job payload must be a JSON object.")

    return cast(JSONObject, normalized)


def _normalize_required_string(
    value: str,
    *,
    field_name: str,
    maximum_length: int,
) -> str:
    if not isinstance(value, str):
        raise BackgroundJobInvalidConfigurationError(f"{field_name} must be a string.")

    normalized = value.strip()

    if not normalized:
        raise BackgroundJobInvalidConfigurationError(f"{field_name} must not be empty.")

    if len(normalized) > maximum_length:
        raise BackgroundJobInvalidConfigurationError(
            f"{field_name} must contain at most {maximum_length} characters."
        )

    return normalized


def _normalize_optional_string(
    value: str | None,
    *,
    field_name: str,
    maximum_length: int,
) -> str | None:
    if value is None:
        return None

    return _normalize_required_string(
        value,
        field_name=field_name,
        maximum_length=maximum_length,
    )


def _normalize_integer(
    value: int,
    *,
    field_name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BackgroundJobInvalidConfigurationError(f"{field_name} must be an integer.")

    if value < -_MAX_INTEGER - 1 or value > _MAX_INTEGER:
        raise BackgroundJobInvalidConfigurationError(
            f"{field_name} exceeds the supported integer range."
        )

    return value


def _normalize_positive_integer(
    value: int,
    *,
    field_name: str,
) -> int:
    normalized = _normalize_integer(
        value,
        field_name=field_name,
    )

    if normalized < 1:
        raise BackgroundJobInvalidConfigurationError(
            f"{field_name} must be greater than or equal to 1."
        )

    return normalized


def _normalize_available_at(
    value: datetime | None,
) -> datetime | None:
    if value is None:
        return None

    if not isinstance(value, datetime):
        raise BackgroundJobInvalidConfigurationError("available_at must be a datetime.")

    if value.tzinfo is None or value.utcoffset() is None:
        raise BackgroundJobInvalidConfigurationError(
            "available_at must include timezone information."
        )

    return value


def _is_semantically_equivalent(
    existing: BackgroundJob,
    *,
    job_type: str,
    payload_version: int,
    payload: JSONObject,
) -> bool:
    return (
        existing.job_type == job_type
        and existing.payload_version == payload_version
        and existing.payload == payload
    )


__all__ = ["EnqueueBackgroundJobService"]
