from datetime import datetime
from typing import Protocol

from clinicops.jobs.contracts import (
    RecoveredBackgroundJobs,
    RecoverStaleBackgroundJobsCommand,
)
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.exceptions import (
    BackgroundJobInvalidConfigurationError,
    BackgroundJobInvalidTransitionError,
)
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.retry import BackgroundJobRetryPolicy

_MAX_BATCH_SIZE = 100
_RECOVERY_ERROR_CODE = "processing_lease_expired"
_RECOVERY_ERROR_MESSAGE = "The processing lease expired before job completion was recorded."


class BackgroundJobRecoveryRepository(Protocol):
    def get_database_time(self) -> datetime:
        """Return the current PostgreSQL timestamp."""

    def find_stale_for_update_skip_locked(
        self,
        *,
        batch_size: int,
        stale_before: datetime,
    ) -> list[BackgroundJob]:
        """Lock and return processing jobs with expired leases."""

    def flush(self) -> None:
        """Flush pending job mutations."""


class RecoverStaleBackgroundJobsService:
    def __init__(
        self,
        repository: BackgroundJobRecoveryRepository,
        retry_policy: BackgroundJobRetryPolicy,
    ) -> None:
        self._repository = repository
        self._retry_policy = retry_policy

    def execute(
        self,
        command: RecoverStaleBackgroundJobsCommand,
    ) -> RecoveredBackgroundJobs:
        batch_size = _normalize_batch_size(command.batch_size)
        recovered_at = self._repository.get_database_time()

        jobs = self._repository.find_stale_for_update_skip_locked(
            batch_size=batch_size,
            stale_before=recovered_at,
        )

        if not jobs:
            return RecoveredBackgroundJobs(
                recovered_for_retry=(),
                dead_lettered=(),
            )

        recovered_for_retry = []
        dead_lettered = []

        for job in jobs:
            _validate_stale_job(
                job,
                recovered_at=recovered_at,
            )

            job.last_error_code = _RECOVERY_ERROR_CODE
            job.last_error_message = _RECOVERY_ERROR_MESSAGE
            job.last_failed_at = recovered_at
            job.completed_at = None

            if job.processing_attempt_count >= job.max_attempts:
                job.status = BackgroundJobStatus.DEAD_LETTERED
                job.dead_lettered_at = recovered_at
                dead_lettered.append(job.id)
            else:
                retry_delay = self._retry_policy.calculate_delay(
                    attempt_number=(job.processing_attempt_count)
                )

                job.status = BackgroundJobStatus.RETRY_SCHEDULED
                job.available_at = recovered_at + retry_delay
                job.dead_lettered_at = None
                recovered_for_retry.append(job.id)

            _clear_claim_ownership(job)

        self._repository.flush()

        return RecoveredBackgroundJobs(
            recovered_for_retry=tuple(recovered_for_retry),
            dead_lettered=tuple(dead_lettered),
        )


def _validate_stale_job(
    job: BackgroundJob,
    *,
    recovered_at: datetime,
) -> None:
    if job.status is not BackgroundJobStatus.PROCESSING:
        raise BackgroundJobInvalidTransitionError(
            job_id=job.id,
            current_status=job.status.value,
            target_status=(BackgroundJobStatus.RETRY_SCHEDULED.value),
        )

    if job.lease_expires_at is None:
        raise BackgroundJobInvalidConfigurationError(
            "A processing job selected for recovery must have a lease expiration timestamp."
        )

    if job.lease_expires_at > recovered_at:
        raise BackgroundJobInvalidConfigurationError(
            "A processing job cannot be recovered before its lease expires."
        )

    if job.processing_attempt_count < 1:
        raise BackgroundJobInvalidConfigurationError(
            "A processing job must have consumed at least one attempt before stale recovery."
        )


def _clear_claim_ownership(job: BackgroundJob) -> None:
    job.worker_id = None
    job.claim_token = None
    job.claimed_at = None
    job.lease_expires_at = None


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


__all__ = ["RecoverStaleBackgroundJobsService"]
