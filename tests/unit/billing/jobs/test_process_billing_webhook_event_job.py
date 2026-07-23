from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.enums import AuditActorType, AuditSource
from clinicops.billing.enums import (
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    BillingWebhookProcessingOutcome,
)
from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
)
from clinicops.billing.jobs.process_billing_webhook_event import (
    ProcessBillingWebhookEventJobHandler,
    SqlAlchemyBillingWebhookEventProcessor,
)
from clinicops.billing.webhooks.process import (
    ProcessBillingWebhookEventCommand,
    ProcessBillingWebhookEventService,
    ProcessedBillingWebhookEvent,
)
from clinicops.jobs.contracts import (
    ClaimedBackgroundJob,
    JSONObject,
)
from clinicops.jobs.runtime.exceptions import (
    InvalidJobPayloadError,
    RetryableJobExecutionError,
    TerminalJobExecutionError,
    UnsupportedJobPayloadVersionError,
)


@dataclass
class RecordingWebhookProcessor:
    processed_event_ids: list[UUID] = field(default_factory=list)
    received_audit_contexts: list[AuditRecordingContext] = field(
        default_factory=list,
    )
    error: Exception | None = None

    def __call__(
        self,
        webhook_event_id: UUID,
        audit_context: AuditRecordingContext,
    ) -> None:
        self.processed_event_ids.append(webhook_event_id)
        self.received_audit_contexts.append(audit_context)

        if self.error is not None:
            raise self.error


class RecordingSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.enter_count = 0

    def __enter__(self) -> "RecordingSession":
        self.enter_count += 1
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> Literal[False]:
        del exc_type, exc, traceback
        self.close()
        return False

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1


class RecordingSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[RecordingSession] = []

    def __call__(self) -> RecordingSession:
        session = RecordingSession()
        self.sessions.append(session)
        return session


class RecordingProcessBillingWebhookEventService(ProcessBillingWebhookEventService):
    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self.error = error
        self.received_sessions: list[object] = []
        self.received_commands: list[ProcessBillingWebhookEventCommand] = []

    def execute(
        self,
        session: Session,
        command: ProcessBillingWebhookEventCommand,
    ) -> ProcessedBillingWebhookEvent:
        self.received_sessions.append(session)
        self.received_commands.append(command)

        if self.error is not None:
            raise self.error

        return ProcessedBillingWebhookEvent(
            webhook_event_id=command.webhook_event_id,
            provider_event_id="evt_test",
            event_type=BillingWebhookEventType.SUBSCRIPTION_RENEWED,
            status=BillingWebhookEventStatus.PROCESSED,
            outcome=BillingWebhookProcessingOutcome.APPLIED,
            subscription_id=uuid4(),
            processing_attempt_count=1,
        )


def _claimed_job(
    *,
    job_type: str = (BILLING_WEBHOOK_PROCESS_JOB_TYPE),
    payload_version: int = 1,
    payload: JSONObject | None = None,
    correlation_id: str | None = None,
    origin_request_id: str | None = None,
    include_origin_request_id: bool = True,
) -> ClaimedBackgroundJob:
    claimed_at = datetime.now(UTC)
    resolved_correlation_id = (
        correlation_id if correlation_id is not None else f"correlation-{uuid4()}"
    )
    resolved_origin_request_id: str | None
    if not include_origin_request_id:
        resolved_origin_request_id = None
    elif origin_request_id is not None:
        resolved_origin_request_id = origin_request_id
    else:
        resolved_origin_request_id = f"request-{uuid4()}"

    return ClaimedBackgroundJob(
        job_id=uuid4(),
        job_type=job_type,
        payload_version=payload_version,
        payload=(
            payload
            if payload is not None
            else {
                "webhook_event_id": str(uuid4()),
            }
        ),
        processing_attempt_count=1,
        max_attempts=5,
        worker_id="worker-1",
        claim_token=uuid4(),
        claimed_at=claimed_at,
        lease_expires_at=(claimed_at + timedelta(minutes=5)),
        correlation_id=resolved_correlation_id,
        origin_request_id=resolved_origin_request_id,
    )


def test_handler_exposes_stable_registry_contract() -> None:
    handler = ProcessBillingWebhookEventJobHandler(RecordingWebhookProcessor())

    assert handler.job_type == "billing.webhook.process"
    assert handler.supported_payload_version == 1


def test_handler_invokes_webhook_processor_with_worker_audit_context() -> None:
    webhook_event_id = uuid4()
    correlation_id = f"correlation-{uuid4()}"
    origin_request_id = f"request-{uuid4()}"
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    handler.execute(
        _claimed_job(
            payload={
                "webhook_event_id": str(webhook_event_id),
            },
            correlation_id=correlation_id,
            origin_request_id=origin_request_id,
        )
    )

    assert processor.processed_event_ids == [webhook_event_id]
    assert len(processor.received_audit_contexts) == 1
    audit_context = processor.received_audit_contexts[0]
    assert audit_context.actor.actor_type is AuditActorType.SYSTEM
    assert audit_context.actor.user_id is None
    assert audit_context.actor.role is None
    assert audit_context.source is AuditSource.WORKER
    assert audit_context.correlation_id == correlation_id
    assert audit_context.request_id == origin_request_id


def test_handler_preserves_missing_origin_request_id() -> None:
    webhook_event_id = uuid4()
    correlation_id = f"correlation-{uuid4()}"
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    handler.execute(
        _claimed_job(
            payload={
                "webhook_event_id": str(webhook_event_id),
            },
            correlation_id=correlation_id,
            include_origin_request_id=False,
        )
    )

    assert processor.processed_event_ids == [webhook_event_id]
    audit_context = processor.received_audit_contexts[0]
    assert audit_context.correlation_id == correlation_id
    assert audit_context.request_id is None
    assert audit_context.actor.actor_type is AuditActorType.SYSTEM
    assert audit_context.source is AuditSource.WORKER


def test_handler_rejects_unexpected_job_type() -> None:
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(TerminalJobExecutionError) as exception_info:
        handler.execute(_claimed_job(job_type=("billing.subscription.reconcile")))

    assert exception_info.value.error_code == "unexpected_billing_job_type"
    assert processor.processed_event_ids == []
    assert processor.received_audit_contexts == []


def test_handler_rejects_unsupported_payload_version() -> None:
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(UnsupportedJobPayloadVersionError):
        handler.execute(_claimed_job(payload_version=2))

    assert processor.processed_event_ids == []


def test_handler_rejects_invalid_payload() -> None:
    processor = RecordingWebhookProcessor()
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(InvalidJobPayloadError):
        handler.execute(_claimed_job(payload={}))

    assert processor.processed_event_ids == []


def test_handler_preserves_retryable_classification() -> None:
    processor = RecordingWebhookProcessor(
        error=RetryableJobExecutionError(
            "The billing dependency is unavailable.",
            error_code="billing_dependency_unavailable",
        )
    )
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(RetryableJobExecutionError) as exception_info:
        handler.execute(_claimed_job())

    assert exception_info.value.error_code == "billing_dependency_unavailable"


def test_handler_preserves_terminal_classification() -> None:
    processor = RecordingWebhookProcessor(
        error=TerminalJobExecutionError(
            "The billing event cannot be processed.",
            error_code="billing_event_terminal",
        )
    )
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(TerminalJobExecutionError) as exception_info:
        handler.execute(_claimed_job())

    assert exception_info.value.error_code == "billing_event_terminal"


def test_handler_allows_unexpected_error_to_reach_runtime() -> None:
    unexpected_error = RuntimeError("unexpected processor failure")
    processor = RecordingWebhookProcessor(error=unexpected_error)
    handler = ProcessBillingWebhookEventJobHandler(processor)

    with pytest.raises(RuntimeError) as exception_info:
        handler.execute(_claimed_job())

    assert exception_info.value is unexpected_error


def test_handler_requires_callable_processor() -> None:
    with pytest.raises(TypeError):
        ProcessBillingWebhookEventJobHandler(
            object()  # type: ignore[arg-type]
        )


def test_sqlalchemy_processor_creates_fresh_session_and_invokes_service() -> None:
    webhook_event_id = uuid4()
    audit_context = AuditRecordingContext.worker_system(
        correlation_id="processor-correlation",
        request_id="processor-request",
    )
    session_factory = RecordingSessionFactory()
    process_service = RecordingProcessBillingWebhookEventService()
    processor = SqlAlchemyBillingWebhookEventProcessor(
        cast(Callable[[], Session], session_factory),
        process_service=process_service,
    )

    processor(webhook_event_id, audit_context)

    assert len(session_factory.sessions) == 1
    session = session_factory.sessions[0]
    assert session.enter_count == 1
    assert session.close_count == 1
    assert len(process_service.received_sessions) == 1
    assert process_service.received_sessions[0] is session
    assert len(process_service.received_commands) == 1
    assert process_service.received_commands[0] == (
        ProcessBillingWebhookEventCommand(
            webhook_event_id=webhook_event_id,
            audit_context=audit_context,
        )
    )


def test_sqlalchemy_processor_commits_once_after_success() -> None:
    session_factory = RecordingSessionFactory()
    process_service = RecordingProcessBillingWebhookEventService()
    processor = SqlAlchemyBillingWebhookEventProcessor(
        cast(Callable[[], Session], session_factory),
        process_service=process_service,
    )

    processor(
        uuid4(),
        AuditRecordingContext.worker_system(
            correlation_id="commit-correlation",
        ),
    )

    session = session_factory.sessions[0]
    assert session.commit_count == 1
    assert session.rollback_count == 0


def test_sqlalchemy_processor_rolls_back_and_reraises_on_failure() -> None:
    unexpected_error = RuntimeError("processing failed")
    session_factory = RecordingSessionFactory()
    process_service = RecordingProcessBillingWebhookEventService(
        error=unexpected_error,
    )
    processor = SqlAlchemyBillingWebhookEventProcessor(
        cast(Callable[[], Session], session_factory),
        process_service=process_service,
    )

    with pytest.raises(RuntimeError) as exception_info:
        processor(
            uuid4(),
            AuditRecordingContext.worker_system(
                correlation_id="rollback-correlation",
            ),
        )

    session = session_factory.sessions[0]
    assert exception_info.value is unexpected_error
    assert session.rollback_count == 1
    assert session.commit_count == 0
    assert session.close_count == 1


def test_sqlalchemy_processor_uses_separate_sessions_per_call() -> None:
    session_factory = RecordingSessionFactory()
    process_service = RecordingProcessBillingWebhookEventService()
    processor = SqlAlchemyBillingWebhookEventProcessor(
        cast(Callable[[], Session], session_factory),
        process_service=process_service,
    )

    first_event_id = uuid4()
    second_event_id = uuid4()
    first_context = AuditRecordingContext.worker_system(
        correlation_id="first-correlation",
    )
    second_context = AuditRecordingContext.worker_system(
        correlation_id="second-correlation",
        request_id="second-request",
    )

    processor(first_event_id, first_context)
    processor(second_event_id, second_context)

    assert len(session_factory.sessions) == 2
    assert len(process_service.received_sessions) == 2
    assert process_service.received_sessions[0] is (session_factory.sessions[0])
    assert process_service.received_sessions[1] is (session_factory.sessions[1])
    assert process_service.received_sessions[0] is not (process_service.received_sessions[1])
    assert [command.webhook_event_id for command in process_service.received_commands] == [
        first_event_id,
        second_event_id,
    ]
    assert process_service.received_commands[0].audit_context is first_context
    assert process_service.received_commands[1].audit_context is second_context


def test_sqlalchemy_processor_defaults_to_real_processing_service() -> None:
    session_factory = RecordingSessionFactory()

    processor = SqlAlchemyBillingWebhookEventProcessor(
        cast(Callable[[], Session], session_factory),
    )

    assert isinstance(
        processor._process_service,
        ProcessBillingWebhookEventService,
    )
    assert type(processor._process_service) is (ProcessBillingWebhookEventService)
