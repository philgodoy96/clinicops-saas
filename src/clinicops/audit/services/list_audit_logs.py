from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from clinicops.audit.contracts import (
    AuditLogPage,
    AuditLogQuery,
)
from clinicops.audit.cursor import (
    decode_audit_log_cursor,
    encode_audit_log_cursor,
)
from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)
from clinicops.audit.policies import (
    ensure_can_read_audit_logs,
)
from clinicops.audit.schemas import (
    AuditLogPageResponse,
    AuditLogResponse,
)


@dataclass(frozen=True, slots=True)
class ListAuditLogsCommand:
    tenant_id: UUID
    membership_role: str
    limit: int = 50
    cursor: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None


class AuditLogReader(Protocol):
    def list_page_for_tenant(
        self,
        query: AuditLogQuery,
    ) -> AuditLogPage:
        """Return one tenant-scoped audit-log page."""

        ...


class ListAuditLogsService:
    def __init__(
        self,
        repository: AuditLogReader,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: ListAuditLogsCommand,
    ) -> AuditLogPageResponse:
        if not isinstance(
            command,
            ListAuditLogsCommand,
        ):
            raise AuditLogInvalidConfigurationError("command must be a ListAuditLogsCommand.")

        if not isinstance(command.tenant_id, UUID):
            raise AuditLogInvalidConfigurationError("tenant_id must be a UUID.")

        ensure_can_read_audit_logs(command.membership_role)

        decoded_cursor = (
            decode_audit_log_cursor(command.cursor) if command.cursor is not None else None
        )

        page = self._repository.list_page_for_tenant(
            AuditLogQuery(
                tenant_id=command.tenant_id,
                limit=command.limit,
                cursor=decoded_cursor,
                action=command.action,
                resource_type=command.resource_type,
                resource_id=command.resource_id,
            )
        )

        return AuditLogPageResponse(
            items=tuple(AuditLogResponse.from_record(record) for record in page.items),
            next_cursor=(
                encode_audit_log_cursor(page.next_cursor) if page.next_cursor is not None else None
            ),
        )


__all__ = [
    "AuditLogReader",
    "ListAuditLogsCommand",
    "ListAuditLogsService",
]
