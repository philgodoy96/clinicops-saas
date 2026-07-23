from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from clinicops.audit.contracts import (
    RecordAuditLogCommand,
    RecordedAuditLog,
)
from clinicops.audit.repositories.audit_log_repository import (
    AuditLogRepository,
)
from clinicops.audit.services.record_audit_log import (
    RecordAuditLogService,
)


class AuditRecorder(Protocol):
    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        """Record an audit entry in the caller transaction."""

        ...


class AuditRecordingService(Protocol):
    def execute(
        self,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        """Execute durable audit recording."""

        ...


type AuditRecordingServiceFactory = Callable[
    [Session],
    AuditRecordingService,
]


class SqlAlchemyAuditRecorder:
    def __init__(
        self,
        service_factory: (AuditRecordingServiceFactory | None) = None,
    ) -> None:
        if service_factory is not None and not callable(service_factory):
            raise TypeError("service_factory must be callable.")

        self._service_factory = (
            service_factory if service_factory is not None else _build_recording_service
        )

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        if not isinstance(
            command,
            RecordAuditLogCommand,
        ):
            raise TypeError("command must be a RecordAuditLogCommand.")

        service = self._service_factory(session)

        return service.execute(command)


def _build_recording_service(
    session: Session,
) -> RecordAuditLogService:
    return RecordAuditLogService(AuditLogRepository(session))


__all__ = [
    "AuditRecorder",
    "AuditRecordingService",
    "AuditRecordingServiceFactory",
    "SqlAlchemyAuditRecorder",
]
