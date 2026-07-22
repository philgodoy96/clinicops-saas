from datetime import datetime, timedelta
from typing import Protocol, cast
from uuid import uuid4

from clinicops.jobs.contracts import (
    ClaimBackgroundJobsCommand,
    ClaimedBackgroundJob,
    JSONObject,
)
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import (
    BackgroundJobInvalidConfigurationError,
    BackgroundJobInvalidTransitionError,
)
from clinicops.jobs.models import BackgroundJob

_MAX_BATCH_SIZE = 100
_MAX_LEASE_DURATION = timedelta(hours=24)


class BackgroundJobClaimRepository(Protocol):
    def get_database_time(self) -> datetime:
        """Return the current PostgreSQL timestamp."""

    def claim_available_for_update_skip_locked(
        self,
        *,
        batch_size: int,
    ) -> list[BackgroundJob]:
        """Lock and return eligible jobs."""

    def flush(self) -> None:
        """Flush pending job mutations."""


class ClaimBackgroundJobsService:
    def __init__(
        self,
        repository: BackgroundJobClaimRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: ClaimBackgroundJobsCommand,
    ) -> tuple[ClaimedBackgroundJob, ...]:
        worker_id = _normalize_worker_id(command.worker_id)
        batch_size = _normalize_batch_size(command.batch_size)
        lease_duration = _normalize_lease_duration(command.lease_duration)

        jobs = self._repository.claim_available_for_update_skip_locked(batch_size=batch_size)

        if not jobs:
            return ()

        claimed_at = self._repository.get_database_time()
        lease_expires_at = claimed_at + lease_duration

        claimed_jobs: list[ClaimedBackgroundJob] = []

        for job in jobs:
            _validate_claimable(job)

            claim_token = uuid4()

            job.status = BackgroundJobStatus.PROCESSING
            job.processing_attempt_count += 1
            job.worker_id = worker_id
            job.claim_token = claim_token
            job.claimed_at = claimed_at
            job.lease_expires_at = lease_expires_at

            claimed_jobs.append(
                ClaimedBackgroundJob(
                    job_id=job.id,
                    job_type=job.job_type,
                    payload_version=job.payload_version,
                    payload=cast(JSONObject, job.payload),
                    processing_attempt_count=(job.processing_attempt_count),
                    max_attempts=job.max_attempts,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    claimed_at=claimed_at,
                    lease_expires_at=lease_expires_at,
                    correlation_id=job.correlation_id,
                    origin_request_id=job.origin_request_id,
                )
            )

        self._repository.flush()

        return tuple(claimed_jobs)


def _validate_claimable(job: BackgroundJob) -> None:
    if job.status not in {
        BackgroundJobStatus.QUEUED,
        BackgroundJobStatus.RETRY_SCHEDULED,
    }:
        raise BackgroundJobInvalidTransitionError(
            job_id=job.id,
            current_status=job.status.value,
            target_status=BackgroundJobStatus.PROCESSING.value,
        )

    if job.processing_attempt_count >= job.max_attempts:
        raise BackgroundJobInvalidTransitionError(
            job_id=job.id,
            current_status=job.status.value,
            target_status=BackgroundJobStatus.PROCESSING.value,
        )


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


def _normalize_batch_size(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BackgroundJobInvalidConfigurationError("batch_size must be an integer.")

    if value < 1:
        raise BackgroundJobInvalidConfigurationError(
            "batch_size must be greater than or equal to 1."
        )

    if value > _MAX_BATCH_SIZE:
        raise BackgroundJobInvalidConfigurationError(
            f"batch_size must not exceed {_MAX_BATCH_SIZE}."
        )

    return value


def _normalize_lease_duration(
    value: timedelta,
) -> timedelta:
    if not isinstance(value, timedelta):
        raise BackgroundJobInvalidConfigurationError("lease_duration must be a timedelta.")

    if value <= timedelta(0):
        raise BackgroundJobInvalidConfigurationError("lease_duration must be greater than zero.")

    if value > _MAX_LEASE_DURATION:
        raise BackgroundJobInvalidConfigurationError("lease_duration must not exceed 24 hours.")

    return value


__all__ = ["ClaimBackgroundJobsService"]
