from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.audit.contracts import (
    AuditLogRecord,
)
from clinicops.audit.enums import (
    AuditActorType,
    AuditSource,
)
from clinicops.audit.schemas import (
    AuditLogResponse,
)


def _record() -> AuditLogRecord:
    return AuditLogRecord(
        audit_log_id=uuid4(),
        tenant_id=uuid4(),
        actor_type=AuditActorType.USER,
        actor_user_id=uuid4(),
        actor_role="ADMIN",
        source=AuditSource.HTTP,
        action="membership.role_changed",
        resource_type="membership",
        resource_id=str(uuid4()),
        metadata_version=1,
        metadata={
            "new_role": "ADMIN",
            "nested": {
                "previous_role": "STAFF",
            },
        },
        idempotency_key="internal-audit-key",
        request_id="request-123",
        correlation_id="correlation-123",
        recorded_at=datetime(
            2026,
            7,
            23,
            12,
            0,
            tzinfo=UTC,
        ),
    )


def test_schema_maps_immutable_audit_record() -> None:
    record = _record()

    response = AuditLogResponse.from_record(record)

    assert response.id == record.audit_log_id
    assert response.tenant_id == record.tenant_id
    assert response.actor.type is (AuditActorType.USER)
    assert response.actor.user_id == record.actor_user_id
    assert response.actor.role == "ADMIN"
    assert response.source is AuditSource.HTTP
    assert response.action == "membership.role_changed"
    assert response.resource.type == "membership"
    assert response.resource.id == record.resource_id
    assert response.metadata_version == 1
    assert response.request_id == "request-123"
    assert response.correlation_id == "correlation-123"
    assert response.recorded_at == record.recorded_at


def test_schema_does_not_expose_idempotency_key() -> None:
    response = AuditLogResponse.from_record(_record())

    dumped = response.model_dump()

    assert "idempotency_key" not in dumped
    assert "idempotency_key" not in (response.model_json_schema()["properties"])


def test_schema_copies_metadata() -> None:
    record = _record()

    response = AuditLogResponse.from_record(record)

    assert response.metadata == record.metadata
    assert response.metadata is not record.metadata
    assert response.metadata["nested"] is not record.metadata["nested"]


def test_schema_rejects_non_record_input() -> None:
    with pytest.raises(
        TypeError,
        match="must be an AuditLogRecord",
    ):
        AuditLogResponse.from_record(
            object(),  # type: ignore[arg-type]
        )
