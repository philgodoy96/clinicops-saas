from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from clinicops.audit.contracts import (
    AuditLogCursor,
    AuditLogPage,
    AuditLogQuery,
    AuditLogRecord,
)
from clinicops.audit.cursor import (
    decode_audit_log_cursor,
    encode_audit_log_cursor,
)
from clinicops.audit.enums import (
    AuditActorType,
    AuditSource,
)
from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)
from clinicops.audit.policies import (
    AuditLogAccessDeniedError,
)
from clinicops.audit.services.list_audit_logs import (
    ListAuditLogsCommand,
    ListAuditLogsService,
)


class RecordingAuditLogReader:
    def __init__(
        self,
        *,
        page: AuditLogPage | None = None,
    ) -> None:
        self.page = (
            page
            if page is not None
            else AuditLogPage(
                items=(),
                next_cursor=None,
            )
        )
        self.queries: list[AuditLogQuery] = []

    def list_page_for_tenant(
        self,
        query: AuditLogQuery,
    ) -> AuditLogPage:
        self.queries.append(query)
        return self.page


def _record(
    *,
    tenant_id: UUID,
    recorded_at: datetime,
) -> AuditLogRecord:
    return AuditLogRecord(
        audit_log_id=uuid4(),
        tenant_id=tenant_id,
        actor_type=AuditActorType.SYSTEM,
        actor_user_id=None,
        actor_role=None,
        source=AuditSource.WORKER,
        action="billing.webhook.processed",
        resource_type="billing_webhook_event",
        resource_id=str(uuid4()),
        metadata_version=1,
        metadata={
            "processing_outcome": "processed",
        },
        idempotency_key=("internal-idempotency-key"),
        request_id="request-123",
        correlation_id="correlation-123",
        recorded_at=recorded_at,
    )


def test_service_propagates_tenant_and_filters() -> None:
    repository = RecordingAuditLogReader()
    service = ListAuditLogsService(repository)
    tenant_id = uuid4()

    response = service.execute(
        ListAuditLogsCommand(
            tenant_id=tenant_id,
            membership_role="ADMIN",
            limit=25,
            action="membership.role_changed",
            resource_type="membership",
            resource_id="membership-123",
        )
    )

    assert response.items == ()
    assert response.next_cursor is None
    assert len(repository.queries) == 1

    query = repository.queries[0]

    assert query.tenant_id == tenant_id
    assert query.limit == 25
    assert query.action == "membership.role_changed"
    assert query.resource_type == "membership"
    assert query.resource_id == "membership-123"
    assert query.cursor is None


def test_service_decodes_incoming_cursor() -> None:
    repository = RecordingAuditLogReader()
    service = ListAuditLogsService(repository)
    cursor = AuditLogCursor(
        recorded_at=datetime(
            2026,
            7,
            23,
            12,
            0,
            tzinfo=UTC,
        ),
        audit_log_id=uuid4(),
    )

    service.execute(
        ListAuditLogsCommand(
            tenant_id=uuid4(),
            membership_role="OWNER",
            cursor=encode_audit_log_cursor(cursor),
        )
    )

    assert repository.queries[0].cursor == cursor


def test_service_encodes_next_cursor() -> None:
    tenant_id = uuid4()
    recorded_at = datetime(
        2026,
        7,
        23,
        12,
        0,
        tzinfo=UTC,
    )
    record = _record(
        tenant_id=tenant_id,
        recorded_at=recorded_at,
    )
    next_cursor = AuditLogCursor(
        recorded_at=record.recorded_at,
        audit_log_id=record.audit_log_id,
    )
    repository = RecordingAuditLogReader(
        page=AuditLogPage(
            items=(record,),
            next_cursor=next_cursor,
        )
    )

    response = ListAuditLogsService(repository).execute(
        ListAuditLogsCommand(
            tenant_id=tenant_id,
            membership_role="OWNER",
        )
    )

    assert len(response.items) == 1
    assert response.items[0].id == record.audit_log_id
    assert response.next_cursor is not None
    assert decode_audit_log_cursor(response.next_cursor) == next_cursor


def test_service_omits_internal_idempotency_key() -> None:
    tenant_id = uuid4()
    record = _record(
        tenant_id=tenant_id,
        recorded_at=datetime.now(UTC),
    )
    repository = RecordingAuditLogReader(
        page=AuditLogPage(
            items=(record,),
            next_cursor=None,
        )
    )

    response = ListAuditLogsService(repository).execute(
        ListAuditLogsCommand(
            tenant_id=tenant_id,
            membership_role="ADMIN",
        )
    )

    dumped_item = response.items[0].model_dump()

    assert "idempotency_key" not in dumped_item


def test_staff_is_denied_before_repository_query() -> None:
    repository = RecordingAuditLogReader()

    with pytest.raises(
        AuditLogAccessDeniedError,
    ):
        ListAuditLogsService(repository).execute(
            ListAuditLogsCommand(
                tenant_id=uuid4(),
                membership_role="STAFF",
            )
        )

    assert repository.queries == []


def test_invalid_cursor_is_rejected_before_repository_query() -> None:
    repository = RecordingAuditLogReader()

    with pytest.raises(
        AuditLogInvalidConfigurationError,
    ):
        ListAuditLogsService(repository).execute(
            ListAuditLogsCommand(
                tenant_id=uuid4(),
                membership_role="ADMIN",
                cursor="not*valid",
            )
        )

    assert repository.queries == []


def test_service_requires_command_contract() -> None:
    repository = RecordingAuditLogReader()

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="must be a ListAuditLogsCommand",
    ):
        ListAuditLogsService(repository).execute(
            object(),  # type: ignore[arg-type]
        )

    assert repository.queries == []


def test_service_requires_uuid_tenant_id() -> None:
    repository = RecordingAuditLogReader()

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="tenant_id must be a UUID",
    ):
        ListAuditLogsService(repository).execute(
            ListAuditLogsCommand(
                tenant_id="not-a-uuid",  # type: ignore[arg-type]
                membership_role="ADMIN",
            )
        )

    assert repository.queries == []
