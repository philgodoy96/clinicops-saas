from datetime import datetime
from typing import Protocol
from uuid import UUID

from clinicops.jobs.contracts import (
    CompleteBackgroundJobCommand,
    CompletedBackgroundJob,
)
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import (
    BackgroundJobClaimOwnershipError,
    BackgroundJobInvalidConfigurationError,
    BackgroundJobInvalidTransitionError,
    BackgroundJobNotFoundError,
)
from clinicops.jobs.models import BackgroundJob


class BackgroundJobCompletionRepository(Protocol):
    def get_by_id_for_update(
        self,
        job_id: UUID,
    ) -> BackgroundJob | None:
        """Lock and return a background job by ID."""

    def get_database_time(self) -> datetime:
        """Return the current PostgreSQL timestamp."""

    def flush(self) -> None:
        """Flush pending job mutations."""


class CompleteBackgroundJobService:
    def __init__(
        self,
        repository: BackgroundJobCompletionRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: CompleteBackgroundJobCommand,
    ) -> CompletedBackgroundJob:
        job_id = _normalize_uuid(
            command.job_id,
            field_name="job_id",
        )
        worker_id = _normalize_worker_id(command.worker_id)
        claim_token = _normalize_uuid(
            command.claim_token,
            field_name="claim_token",
        )

        job = self._repository.get_by_id_for_update(job_id)

        if job is None:
            raise BackgroundJobNotFoundError(job_id)

        _validate_processing_status(job)
        _validate_claim_ownership(
            job,
            worker_id=worker_id,
            claim_token=claim_token,
        )

        completed_at = self._repository.get_database_time()

        job.status = BackgroundJobStatus.SUCCEEDED
        job.completed_at = completed_at
        job.dead_lettered_at = None

        _clear_claim_ownership(job)

        self._repository.flush()

        return CompletedBackgroundJob(
            job_id=job.id,
            status=job.status,
            completed_at=completed_at,
            processing_attempt_count=(job.processing_attempt_count),
        )


def _validate_processing_status(
    job: BackgroundJob,
) -> None:
    if job.status is not BackgroundJobStatus.PROCESSING:
        raise BackgroundJobInvalidTransitionError(
            job_id=job.id,
            current_status=job.status.value,
            target_status=BackgroundJobStatus.SUCCEEDED.value,
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


def _normalize_uuid(
    value: UUID,
    *,
    field_name: str,
) -> UUID:
    if not isinstance(value, UUID):
        raise BackgroundJobInvalidConfigurationError(f"{field_name} must be a UUID.")

    return value


__all__ = ["CompleteBackgroundJobService"]
