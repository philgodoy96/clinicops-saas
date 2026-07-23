import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from threading import Event
from time import monotonic
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.jobs.contracts import (
    ClaimBackgroundJobsCommand,
    ClaimedBackgroundJob,
    CompleteBackgroundJobCommand,
    CompletedBackgroundJob,
    FailBackgroundJobCommand,
    FailedBackgroundJob,
    RecoveredBackgroundJobs,
    RecoverStaleBackgroundJobsCommand,
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
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.runtime.exceptions import (
    JobExecutionError,
    RetryableJobExecutionError,
    UnsupportedJobPayloadVersionError,
)
from clinicops.jobs.runtime.identity import resolve_worker_id
from clinicops.jobs.runtime.registry import JobHandlerRegistry
from clinicops.jobs.services.claim_background_jobs import (
    ClaimBackgroundJobsService,
)
from clinicops.jobs.services.complete_background_job import (
    CompleteBackgroundJobService,
)
from clinicops.jobs.services.fail_background_job import (
    FailBackgroundJobService,
)
from clinicops.jobs.services.recover_stale_background_jobs import (
    RecoverStaleBackgroundJobsService,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
MonotonicProvider = Callable[[], float]

_CLAIM_LOST_ERRORS = (
    BackgroundJobClaimOwnershipError,
    BackgroundJobInvalidTransitionError,
    BackgroundJobNotFoundError,
)


class WorkerIterationOutcome(StrEnum):
    IDLE = "idle"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"
    CLAIM_LOST = "claim_lost"


@dataclass(frozen=True, slots=True)
class WorkerIterationResult:
    outcome: WorkerIterationOutcome
    job_id: UUID | None = None
    job_type: str | None = None


class BackgroundJobRuntimeStore(Protocol):
    def recover_stale_jobs(
        self,
        *,
        batch_size: int,
    ) -> RecoveredBackgroundJobs:
        """Recover processing jobs with expired leases."""

    def claim_one(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
    ) -> ClaimedBackgroundJob | None:
        """Claim one eligible background job."""

    def complete(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        claim_token: UUID,
    ) -> CompletedBackgroundJob:
        """Record successful completion."""

    def fail(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        claim_token: UUID,
        failure_kind: BackgroundJobFailureKind,
        error_code: str,
        error_message: str,
    ) -> FailedBackgroundJob:
        """Record retryable or terminal failure."""


class SqlAlchemyBackgroundJobRuntimeStore:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        retry_policy: BackgroundJobRetryPolicy,
    ) -> None:
        self._session_factory = session_factory
        self._retry_policy = retry_policy

    def recover_stale_jobs(
        self,
        *,
        batch_size: int,
    ) -> RecoveredBackgroundJobs:
        with self._session_factory() as session:
            try:
                result = RecoverStaleBackgroundJobsService(
                    BackgroundJobRepository(session),
                    self._retry_policy,
                ).execute(
                    RecoverStaleBackgroundJobsCommand(
                        batch_size=batch_size,
                    )
                )
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    def claim_one(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
    ) -> ClaimedBackgroundJob | None:
        with self._session_factory() as session:
            try:
                claims = ClaimBackgroundJobsService(BackgroundJobRepository(session)).execute(
                    ClaimBackgroundJobsCommand(
                        worker_id=worker_id,
                        batch_size=1,
                        lease_duration=lease_duration,
                    )
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

        if not claims:
            return None

        return claims[0]

    def complete(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        claim_token: UUID,
    ) -> CompletedBackgroundJob:
        with self._session_factory() as session:
            try:
                result = CompleteBackgroundJobService(BackgroundJobRepository(session)).execute(
                    CompleteBackgroundJobCommand(
                        job_id=job_id,
                        worker_id=worker_id,
                        claim_token=claim_token,
                    )
                )
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    def fail(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        claim_token: UUID,
        failure_kind: BackgroundJobFailureKind,
        error_code: str,
        error_message: str,
    ) -> FailedBackgroundJob:
        with self._session_factory() as session:
            try:
                result = FailBackgroundJobService(
                    BackgroundJobRepository(session),
                    self._retry_policy,
                ).execute(
                    FailBackgroundJobCommand(
                        job_id=job_id,
                        worker_id=worker_id,
                        claim_token=claim_token,
                        failure_kind=failure_kind,
                        error_code=error_code,
                        error_message=error_message,
                    )
                )
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise


class BackgroundWorker:
    def __init__(
        self,
        *,
        store: BackgroundJobRuntimeStore,
        registry: JobHandlerRegistry,
        worker_id: str,
        poll_interval: timedelta,
        lease_duration: timedelta,
        stale_recovery_interval: timedelta,
        stale_recovery_batch_size: int,
        monotonic_provider: MonotonicProvider = monotonic,
    ) -> None:
        self._store = store
        self._registry = registry
        self._worker_id = resolve_worker_id(worker_id)
        self._poll_interval = _validate_positive_duration(
            poll_interval,
            field_name="poll_interval",
        )
        self._lease_duration = _validate_positive_duration(
            lease_duration,
            field_name="lease_duration",
        )
        self._stale_recovery_interval = _validate_positive_duration(
            stale_recovery_interval,
            field_name="stale_recovery_interval",
        )
        self._stale_recovery_batch_size = _validate_stale_recovery_batch_size(
            stale_recovery_batch_size
        )

        if not callable(monotonic_provider):
            raise BackgroundJobInvalidConfigurationError("monotonic_provider must be callable.")

        self._monotonic_provider = monotonic_provider
        self._next_stale_recovery_at: float | None = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def run_once(self) -> WorkerIterationResult:
        self._recover_stale_jobs_if_due()

        job = self._store.claim_one(
            worker_id=self._worker_id,
            lease_duration=self._lease_duration,
        )

        if job is None:
            return WorkerIterationResult(outcome=WorkerIterationOutcome.IDLE)

        return self._execute_claimed_job(job)

    def run_forever(self, stop_event: Event) -> None:
        logger.info(
            "background_worker_started",
            extra={
                "worker_id": self._worker_id,
            },
        )

        try:
            while not stop_event.is_set():
                try:
                    result = self.run_once()
                except Exception:
                    logger.exception(
                        "background_worker_iteration_failed",
                        extra={
                            "worker_id": self._worker_id,
                        },
                    )
                    stop_event.wait(self._poll_interval.total_seconds())
                    continue

                if result.outcome is WorkerIterationOutcome.IDLE:
                    stop_event.wait(self._poll_interval.total_seconds())
        finally:
            logger.info(
                "background_worker_stopped",
                extra={
                    "worker_id": self._worker_id,
                },
            )

    def _recover_stale_jobs_if_due(self) -> None:
        current_time = self._monotonic_provider()

        if self._next_stale_recovery_at is not None and current_time < self._next_stale_recovery_at:
            return

        result = self._store.recover_stale_jobs(batch_size=self._stale_recovery_batch_size)

        self._next_stale_recovery_at = current_time + self._stale_recovery_interval.total_seconds()

        if result.recovered_for_retry or result.dead_lettered:
            logger.info(
                "background_jobs_recovered",
                extra={
                    "worker_id": self._worker_id,
                    "recovered_for_retry_count": len(result.recovered_for_retry),
                    "dead_lettered_count": len(result.dead_lettered),
                },
            )

    def _execute_claimed_job(
        self,
        job: ClaimedBackgroundJob,
    ) -> WorkerIterationResult:
        started_at = self._monotonic_provider()

        logger.info(
            "background_job_execution_started",
            extra=_job_log_context(
                worker_id=self._worker_id,
                job=job,
            ),
        )

        try:
            handler = self._registry.resolve(job.job_type)

            if job.payload_version != handler.supported_payload_version:
                raise UnsupportedJobPayloadVersionError(
                    job_type=job.job_type,
                    received_version=job.payload_version,
                    supported_version=(handler.supported_payload_version),
                )

            handler.execute(job)
        except JobExecutionError as error:
            result = self._record_failure(
                job,
                error=error,
            )
        except Exception:
            logger.exception(
                "background_job_handler_failed_unexpectedly",
                extra=_job_log_context(
                    worker_id=self._worker_id,
                    job=job,
                ),
            )
            result = self._record_failure(
                job,
                error=RetryableJobExecutionError(
                    "Unexpected exception while executing the background job.",
                    error_code=("unexpected_job_execution_error"),
                ),
            )
        else:
            result = self._record_completion(job)

        logger.info(
            "background_job_execution_finished",
            extra={
                **_job_log_context(
                    worker_id=self._worker_id,
                    job=job,
                ),
                "outcome": result.outcome.value,
                "duration_ms": round(
                    (self._monotonic_provider() - started_at) * 1000,
                    3,
                ),
            },
        )

        return result

    def _record_completion(
        self,
        job: ClaimedBackgroundJob,
    ) -> WorkerIterationResult:
        try:
            self._store.complete(
                job_id=job.job_id,
                worker_id=self._worker_id,
                claim_token=job.claim_token,
            )
        except _CLAIM_LOST_ERRORS:
            logger.warning(
                "background_job_completion_claim_lost",
                extra=_job_log_context(
                    worker_id=self._worker_id,
                    job=job,
                ),
            )
            return WorkerIterationResult(
                outcome=WorkerIterationOutcome.CLAIM_LOST,
                job_id=job.job_id,
                job_type=job.job_type,
            )

        return WorkerIterationResult(
            outcome=WorkerIterationOutcome.SUCCEEDED,
            job_id=job.job_id,
            job_type=job.job_type,
        )

    def _record_failure(
        self,
        job: ClaimedBackgroundJob,
        *,
        error: JobExecutionError,
    ) -> WorkerIterationResult:
        failure_kind = (
            BackgroundJobFailureKind.RETRYABLE
            if error.retryable
            else BackgroundJobFailureKind.TERMINAL
        )

        try:
            failed_job = self._store.fail(
                job_id=job.job_id,
                worker_id=self._worker_id,
                claim_token=job.claim_token,
                failure_kind=failure_kind,
                error_code=error.error_code,
                error_message=str(error),
            )
        except _CLAIM_LOST_ERRORS:
            logger.warning(
                "background_job_failure_claim_lost",
                extra=_job_log_context(
                    worker_id=self._worker_id,
                    job=job,
                ),
            )
            return WorkerIterationResult(
                outcome=WorkerIterationOutcome.CLAIM_LOST,
                job_id=job.job_id,
                job_type=job.job_type,
            )

        if failed_job.status is BackgroundJobStatus.RETRY_SCHEDULED:
            outcome = WorkerIterationOutcome.RETRY_SCHEDULED
        elif failed_job.status is BackgroundJobStatus.DEAD_LETTERED:
            outcome = WorkerIterationOutcome.DEAD_LETTERED
        else:
            raise RuntimeError(
                f"Failure recording returned an unexpected job status: {failed_job.status.value}."
            )

        return WorkerIterationResult(
            outcome=outcome,
            job_id=job.job_id,
            job_type=job.job_type,
        )


def _validate_positive_duration(
    value: timedelta,
    *,
    field_name: str,
) -> timedelta:
    if not isinstance(value, timedelta):
        raise BackgroundJobInvalidConfigurationError(f"{field_name} must be a timedelta.")

    if value <= timedelta(0):
        raise BackgroundJobInvalidConfigurationError(f"{field_name} must be greater than zero.")

    return value


def _validate_stale_recovery_batch_size(
    value: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BackgroundJobInvalidConfigurationError(
            "stale_recovery_batch_size must be an integer."
        )

    if value < 1 or value > 100:
        raise BackgroundJobInvalidConfigurationError(
            "stale_recovery_batch_size must be between 1 and 100."
        )

    return value


def _job_log_context(
    *,
    worker_id: str,
    job: ClaimedBackgroundJob,
) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "job_id": str(job.job_id),
        "job_type": job.job_type,
        "processing_attempt_count": (job.processing_attempt_count),
        "max_attempts": job.max_attempts,
        "claim_token": str(job.claim_token),
        "correlation_id": job.correlation_id,
        "origin_request_id": job.origin_request_id,
    }


__all__ = [
    "BackgroundJobRuntimeStore",
    "BackgroundWorker",
    "MonotonicProvider",
    "SessionFactory",
    "SqlAlchemyBackgroundJobRuntimeStore",
    "WorkerIterationOutcome",
    "WorkerIterationResult",
]
