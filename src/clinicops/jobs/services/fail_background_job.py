from datetime import datetime
from typing import Protocol
from uuid import UUID

from clinicops.jobs.contracts import (
    FailBackgroundJobCommand,
    FailedBackgroundJob,
)
from clinicops.jobs.enums import (
    BackgroundJobFailureKind,
    BackgroundJobStatus,
)
from clinicops.jobs.exceptions import (
    BackgroundJobClaimOwnershipError,
    BackgroundJobInvalidConfigurationError,
    BackgroundJobInvalidTransitionError,
    BackgroundJobNotFoundError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.retry import BackgroundJobRetryPolicy


class BackgroundJobFailureRepository(Protocol):
    def get_by_id_for_update(
        self,
        job_id: UUID,
    ) -> BackgroundJob | None:
        """Lock and return a background job by ID."""

    def get_database_time(self) -> datetime:
        """Return the current PostgreSQL timestamp."""

    def flush(self) -> None:
        """Flush pending job mutations."""


class FailBackgroundJobService:
    def __init__(
        self,
        repository: BackgroundJobFailureRepository,
        retry_policy: BackgroundJobRetryPolicy,
    ) -> None:
        self._repository = repository
        self._retry_policy = retry_policy

    def execute(
        self,
        command: FailBackgroundJobCommand,
    ) -> FailedBackgroundJob:
        job_id = _normalize_uuid(
            command.job_id,
            field_name="job_id",
        )
        worker_id = _normalize_worker_id(command.worker_id)
        claim_token = _normalize_uuid(
            command.claim_token,
            field_name="claim_token",
        )
        failure_kind = _normalize_failure_kind(command.failure_kind)
        error_code = _normalize_error_code(command.error_code)
        error_message = _sanitize_error_message(command.error_message)

        job = self._repository.get_by_id_for_update(job_id)

        if job is None:
            raise BackgroundJobNotFoundError(job_id)

        _validate_processing_status(job)
        _validate_claim_ownership(
            job,
            worker_id=worker_id,
            claim_token=claim_token,
        )

        failed_at = self._repository.get_database_time()

        job.last_error_code = error_code
        job.last_error_message = error_message
        job.last_failed_at = failed_at
        job.completed_at = None

        should_dead_letter = failure_kind is BackgroundJobFailureKind.TERMINAL or (
            job.processing_attempt_count >= job.max_attempts
        )

        if should_dead_letter:
            job.status = BackgroundJobStatus.DEAD_LETTERED
            job.dead_lettered_at = failed_at
        else:
            retry_delay = self._retry_policy.calculate_delay(
                attempt_number=job.processing_attempt_count
            )

            job.status = BackgroundJobStatus.RETRY_SCHEDULED
            job.available_at = failed_at + retry_delay
            job.dead_lettered_at = None

        _clear_claim_ownership(job)

        self._repository.flush()

        return FailedBackgroundJob(
            job_id=job.id,
            status=job.status,
            processing_attempt_count=(job.processing_attempt_count),
            max_attempts=job.max_attempts,
            available_at=job.available_at,
            last_failed_at=failed_at,
            dead_lettered_at=job.dead_lettered_at,
        )


def _validate_processing_status(
    job: BackgroundJob,
) -> None:
    if job.status is not BackgroundJobStatus.PROCESSING:
        raise BackgroundJobInvalidTransitionError(
            job_id=job.id,
            current_status=job.status.value,
            target_status=(BackgroundJobStatus.RETRY_SCHEDULED.value),
        )


def _validate_claim_ownership(
    job: BackgroundJob,
    *,
    worker_id: str,
    claim_token: UUID,
) -> None:
    if job.worker_id != worker_id or job.claim_token != claim_token:
        raise BackgroundJobClaimOwnershipError(job.id)


def _clear_claim_ownership(job: BackgroundJob) -> None:
    job.worker_id = None
    job.claim_token = None
    job.claimed_at = None
    job.lease_expires_at = None


def _normalize_failure_kind(
    value: BackgroundJobFailureKind,
) -> BackgroundJobFailureKind:
    if not isinstance(value, BackgroundJobFailureKind):
        raise BackgroundJobInvalidConfigurationError(
            "failure_kind must be a BackgroundJobFailureKind."
        )

    return value


def _normalize_worker_id(value: str) -> str:
    if not isinstance(value, str):
        raise BackgroundJobInvalidConfigurationError("worker_id must be a string.")

    normalized = value.strip()

    if not normalized:
        raise BackgroundJobInvalidConfigurationError("worker_id must not be empty.")

    if len(normalized) > 255:
        raise BackgroundJobInvalidConfigurationError(
            "worker_id must contain at most 255 characters."
        )

    return normalized


def _normalize_error_code(value: str) -> str:
    if not isinstance(value, str):
        raise BackgroundJobInvalidConfigurationError("error_code must be a string.")

    normalized = value.strip()

    if not normalized:
        raise BackgroundJobInvalidConfigurationError("error_code must not be empty.")

    if len(normalized) > 100:
        raise BackgroundJobInvalidConfigurationError(
            "error_code must contain at most 100 characters."
        )

    return normalized


def _sanitize_error_message(value: str) -> str:
    if not isinstance(value, str):
        raise BackgroundJobInvalidConfigurationError("error_message must be a string.")

    normalized = " ".join(value.split())

    if not normalized:
        raise BackgroundJobInvalidConfigurationError("error_message must not be empty.")

    return normalized[:2000]


def _normalize_uuid(
    value: UUID,
    *,
    field_name: str,
) -> UUID:
    if not isinstance(value, UUID):
        raise BackgroundJobInvalidConfigurationError(f"{field_name} must be a UUID.")

    return value


__all__ = ["FailBackgroundJobService"]
