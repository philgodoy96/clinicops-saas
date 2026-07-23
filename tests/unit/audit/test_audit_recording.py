import inspect
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

import clinicops.audit.recording as recording_module
from clinicops.audit.contracts import (
    AuditActor,
    RecordAuditLogCommand,
    RecordedAuditLog,
)
from clinicops.audit.enums import AuditSource
from clinicops.audit.recording import (
    AuditRecordingService,
    SqlAlchemyAuditRecorder,
)

_RECORDED_AT = datetime(
    2026,
    7,
    23,
    12,
    0,
    tzinfo=UTC,
)


class RecordingAuditService:
    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self.error = error
        self.commands: list[RecordAuditLogCommand] = []
        self.result = RecordedAuditLog(
            audit_log_id=uuid4(),
            created=True,
            recorded_at=_RECORDED_AT,
        )

    def execute(
        self,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        self.commands.append(command)

        if self.error is not None:
            raise self.error

        return self.result


def _command() -> RecordAuditLogCommand:
    return RecordAuditLogCommand(
        tenant_id=uuid4(),
        actor=AuditActor.system(),
        source=AuditSource.WORKER,
        action="billing.webhook.processed",
        resource_type="billing_webhook_event",
        resource_id=str(uuid4()),
        correlation_id="correlation-123",
        metadata={
            "processing_outcome": "processed",
        },
        idempotency_key=(f"billing-webhook-audit:{uuid4()}:processed"),
        request_id="request-123",
    )


def test_recorder_uses_same_caller_session() -> None:
    service = RecordingAuditService()
    sessions: list[Session] = []

    def service_factory(
        session: Session,
    ) -> AuditRecordingService:
        sessions.append(session)
        return service

    recorder = SqlAlchemyAuditRecorder(
        service_factory=service_factory,
    )
    session = cast(Session, object())
    command = _command()

    result = recorder.record(
        session,
        command,
    )

    assert sessions == [session]
    assert service.commands == [command]
    assert result is service.result


def test_recorder_does_not_commit_or_roll_back() -> None:
    service = RecordingAuditService()
    recorder = SqlAlchemyAuditRecorder(service_factory=lambda session: service)

    class TransactionRecordingSession:
        def __init__(self) -> None:
            self.commit_count = 0
            self.rollback_count = 0

        def commit(self) -> None:
            self.commit_count += 1

        def rollback(self) -> None:
            self.rollback_count += 1

    session = TransactionRecordingSession()

    recorder.record(
        cast(Session, session),
        _command(),
    )

    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_recorder_propagates_recording_failure() -> None:
    expected_error = RuntimeError("audit persistence failed")
    service = RecordingAuditService(error=expected_error)
    recorder = SqlAlchemyAuditRecorder(service_factory=lambda session: service)

    with pytest.raises(RuntimeError) as exc_info:
        recorder.record(
            cast(Session, object()),
            _command(),
        )

    assert exc_info.value is expected_error


def test_recorder_builds_independent_service_per_call() -> None:
    created_services: list[RecordingAuditService] = []

    def service_factory(
        session: Session,
    ) -> AuditRecordingService:
        service = RecordingAuditService()
        created_services.append(service)
        return service

    recorder = SqlAlchemyAuditRecorder(
        service_factory=service_factory,
    )
    session = cast(Session, object())

    recorder.record(session, _command())
    recorder.record(session, _command())

    assert len(created_services) == 2
    assert created_services[0] is not created_services[1]


def test_recorder_requires_callable_service_factory() -> None:
    with pytest.raises(
        TypeError,
        match="service_factory must be callable",
    ):
        SqlAlchemyAuditRecorder(
            object(),  # type: ignore[arg-type]
        )


def test_recording_module_has_no_fastapi_dependency() -> None:
    source = inspect.getsource(recording_module)

    assert "fastapi" not in source.lower()
    assert "RequestContext" not in source
    assert "HTTPException" not in source
