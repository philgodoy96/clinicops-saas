from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import UUID, uuid4

from clinicops.jobs.contracts import (
    ClaimedBackgroundJob,
    CompletedBackgroundJob,
    FailedBackgroundJob,
    RecoveredBackgroundJobs,
)
from clinicops.jobs.enums import (
    BackgroundJobFailureKind,
    BackgroundJobStatus,
)
from clinicops.jobs.exceptions import (
    BackgroundJobClaimOwnershipError,
)
from clinicops.jobs.runtime.exceptions import (
    RetryableJobExecutionError,
    TerminalJobExecutionError,
)
from clinicops.jobs.runtime.registry import JobHandlerRegistry
from clinicops.jobs.runtime.worker import (
    BackgroundWorker,
    WorkerIterationOutcome,
)


class FakeClock:
    def __init__(self) -> None:
        self.current = 100.0

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


class RecordingRuntimeStore:
    def __init__(
        self,
        claims: list[ClaimedBackgroundJob] | None = None,
    ) -> None:
        self.claims = claims or []
        self.recovery_count = 0
        self.claim_count = 0
        self.completed_job_ids: list[UUID] = []
        self.failures: list[
            tuple[
                UUID,
                BackgroundJobFailureKind,
                str,
                str,
            ]
        ] = []
        self.completion_error: Exception | None = None
        self.claim_called_event: Event | None = None

    def recover_stale_jobs(
        self,
        *,
        batch_size: int,
    ) -> RecoveredBackgroundJobs:
        self.recovery_count += 1

        return RecoveredBackgroundJobs(
            recovered_for_retry=(),
            dead_lettered=(),
        )

    def claim_one(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
    ) -> ClaimedBackgroundJob | None:
        self.claim_count += 1

        if self.claim_called_event is not None:
            self.claim_called_event.set()

        if not self.claims:
            return None

        return self.claims.pop(0)

    def complete(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        claim_token: UUID,
    ) -> CompletedBackgroundJob:
        if self.completion_error is not None:
            raise self.completion_error

        self.completed_job_ids.append(job_id)

        return CompletedBackgroundJob(
            job_id=job_id,
            status=BackgroundJobStatus.SUCCEEDED,
            completed_at=datetime.now(UTC),
            processing_attempt_count=1,
        )

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
        self.failures.append(
            (
                job_id,
                failure_kind,
                error_code,
                error_message,
            )
        )

        failed_at = datetime.now(UTC)
        is_terminal = failure_kind is BackgroundJobFailureKind.TERMINAL
        status = (
            BackgroundJobStatus.DEAD_LETTERED
            if is_terminal
            else BackgroundJobStatus.RETRY_SCHEDULED
        )

        return FailedBackgroundJob(
            job_id=job_id,
            status=status,
            processing_attempt_count=1,
            max_attempts=5,
            available_at=(failed_at + timedelta(minutes=1)),
            last_failed_at=failed_at,
            dead_lettered_at=(failed_at if is_terminal else None),
        )


@dataclass
class RecordingHandler:
    job_type: str = "billing.webhook.process"
    supported_payload_version: int = 1
    execution_count: int = 0
    error: Exception | None = None

    def execute(
        self,
        job: ClaimedBackgroundJob,
    ) -> None:
        self.execution_count += 1

        if self.error is not None:
            raise self.error


def _claim(
    *,
    job_type: str = "billing.webhook.process",
    payload_version: int = 1,
) -> ClaimedBackgroundJob:
    claimed_at = datetime.now(UTC)

    return ClaimedBackgroundJob(
        job_id=uuid4(),
        job_type=job_type,
        payload_version=payload_version,
        payload={
            "webhook_event_id": str(uuid4()),
        },
        processing_attempt_count=1,
        max_attempts=5,
        worker_id="worker-1",
        claim_token=uuid4(),
        claimed_at=claimed_at,
        lease_expires_at=(claimed_at + timedelta(minutes=5)),
        correlation_id=f"correlation-{uuid4()}",
        origin_request_id=f"request-{uuid4()}",
    )


def _worker(
    *,
    store: RecordingRuntimeStore,
    registry: JobHandlerRegistry | None = None,
    clock: FakeClock | None = None,
    poll_interval: timedelta = timedelta(seconds=1),
) -> BackgroundWorker:
    return BackgroundWorker(
        store=store,
        registry=registry or JobHandlerRegistry(),
        worker_id="worker-1",
        poll_interval=poll_interval,
        lease_duration=timedelta(minutes=5),
        stale_recovery_interval=timedelta(minutes=1),
        stale_recovery_batch_size=50,
        monotonic_provider=clock or FakeClock(),
    )


def test_idle_iteration_returns_idle_result() -> None:
    store = RecordingRuntimeStore()

    result = _worker(store=store).run_once()

    assert result.outcome is WorkerIterationOutcome.IDLE
    assert result.job_id is None
    assert result.job_type is None
    assert store.recovery_count == 1
    assert store.claim_count == 1


def test_successful_handler_completes_job() -> None:
    claim = _claim()
    store = RecordingRuntimeStore([claim])
    handler = RecordingHandler()

    result = _worker(
        store=store,
        registry=JobHandlerRegistry([handler]),
    ).run_once()

    assert result.outcome is WorkerIterationOutcome.SUCCEEDED
    assert result.job_id == claim.job_id
    assert handler.execution_count == 1
    assert store.completed_job_ids == [claim.job_id]
    assert store.failures == []


def test_retryable_handler_failure_schedules_retry() -> None:
    claim = _claim()
    store = RecordingRuntimeStore([claim])
    handler = RecordingHandler(
        error=RetryableJobExecutionError(
            "Provider temporarily unavailable.",
            error_code="provider_unavailable",
        )
    )

    result = _worker(
        store=store,
        registry=JobHandlerRegistry([handler]),
    ).run_once()

    assert result.outcome is WorkerIterationOutcome.RETRY_SCHEDULED
    assert store.failures == [
        (
            claim.job_id,
            BackgroundJobFailureKind.RETRYABLE,
            "provider_unavailable",
            "Provider temporarily unavailable.",
        )
    ]


def test_terminal_handler_failure_dead_letters_job() -> None:
    claim = _claim()
    store = RecordingRuntimeStore([claim])
    handler = RecordingHandler(
        error=TerminalJobExecutionError(
            "The payload cannot be processed.",
            error_code="invalid_payload",
        )
    )

    result = _worker(
        store=store,
        registry=JobHandlerRegistry([handler]),
    ).run_once()

    assert result.outcome is WorkerIterationOutcome.DEAD_LETTERED
    assert store.failures[0][1] is (BackgroundJobFailureKind.TERMINAL)
    assert store.failures[0][2] == "invalid_payload"


def test_unexpected_handler_error_defaults_to_retryable() -> None:
    claim = _claim()
    store = RecordingRuntimeStore([claim])
    handler = RecordingHandler(error=RuntimeError("Sensitive internal detail"))

    result = _worker(
        store=store,
        registry=JobHandlerRegistry([handler]),
    ).run_once()

    assert result.outcome is WorkerIterationOutcome.RETRY_SCHEDULED
    assert store.failures[0][1] is (BackgroundJobFailureKind.RETRYABLE)
    assert store.failures[0][2] == "unexpected_job_execution_error"
    assert store.failures[0][3] == "Unexpected exception while executing the background job."


def test_unknown_job_type_is_terminal() -> None:
    claim = _claim(job_type="unknown.job")
    store = RecordingRuntimeStore([claim])

    result = _worker(store=store).run_once()

    assert result.outcome is WorkerIterationOutcome.DEAD_LETTERED
    assert store.failures[0][1] is (BackgroundJobFailureKind.TERMINAL)
    assert store.failures[0][2] == "unknown_job_type"


def test_unsupported_payload_version_is_terminal() -> None:
    claim = _claim(payload_version=2)
    store = RecordingRuntimeStore([claim])
    handler = RecordingHandler(supported_payload_version=1)

    result = _worker(
        store=store,
        registry=JobHandlerRegistry([handler]),
    ).run_once()

    assert result.outcome is WorkerIterationOutcome.DEAD_LETTERED
    assert store.failures[0][2] == "unsupported_job_payload_version"
    assert handler.execution_count == 0


def test_completion_ownership_loss_returns_claim_lost() -> None:
    claim = _claim()
    store = RecordingRuntimeStore([claim])
    store.completion_error = BackgroundJobClaimOwnershipError(claim.job_id)

    result = _worker(
        store=store,
        registry=JobHandlerRegistry([RecordingHandler()]),
    ).run_once()

    assert result.outcome is WorkerIterationOutcome.CLAIM_LOST


def test_stale_recovery_runs_on_configured_interval() -> None:
    clock = FakeClock()
    store = RecordingRuntimeStore()
    worker = _worker(
        store=store,
        clock=clock,
    )

    worker.run_once()
    worker.run_once()

    assert store.recovery_count == 1

    clock.advance(60)
    worker.run_once()

    assert store.recovery_count == 2


def test_run_forever_idle_wait_is_interruptible() -> None:
    store = RecordingRuntimeStore()
    claim_called = Event()
    stop_event = Event()
    store.claim_called_event = claim_called

    worker = _worker(
        store=store,
        poll_interval=timedelta(seconds=30),
    )
    thread = Thread(
        target=worker.run_forever,
        args=(stop_event,),
    )

    thread.start()

    assert claim_called.wait(timeout=5)

    stop_event.set()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert store.claim_count == 1
