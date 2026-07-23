from copy import deepcopy
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from clinicops.audit.contracts import (
    AuditLogRecord,
    JSONObject,
)
from clinicops.audit.enums import (
    AuditActorType,
    AuditSource,
)


class AuditActorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: AuditActorType
    user_id: UUID | None
    role: str | None


class AuditResourceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    id: str


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    actor: AuditActorResponse
    source: AuditSource
    action: str
    resource: AuditResourceResponse
    metadata_version: int
    metadata: JSONObject
    request_id: str | None
    correlation_id: str
    recorded_at: datetime

    @classmethod
    def from_record(
        cls,
        record: AuditLogRecord,
    ) -> "AuditLogResponse":
        if not isinstance(record, AuditLogRecord):
            raise TypeError("record must be an AuditLogRecord.")

        return cls(
            id=record.audit_log_id,
            tenant_id=record.tenant_id,
            actor=AuditActorResponse(
                type=record.actor_type,
                user_id=record.actor_user_id,
                role=record.actor_role,
            ),
            source=record.source,
            action=record.action,
            resource=AuditResourceResponse(
                type=record.resource_type,
                id=record.resource_id,
            ),
            metadata_version=record.metadata_version,
            metadata=deepcopy(record.metadata),
            request_id=record.request_id,
            correlation_id=record.correlation_id,
            recorded_at=record.recorded_at,
        )


class AuditLogPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[AuditLogResponse, ...]
    next_cursor: str | None


__all__ = [
    "AuditActorResponse",
    "AuditLogPageResponse",
    "AuditLogResponse",
    "AuditResourceResponse",
]
