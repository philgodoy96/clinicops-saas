from copy import deepcopy
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clinicops.audit.contracts import (
    AuditLogCursor,
    AuditLogPage,
    AuditLogQuery,
    AuditLogRecord,
    JSONObject,
)
from clinicops.audit.enums import AuditActorType, AuditSource
from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)
from clinicops.audit.models import AuditLogEntry

_MAX_ACTION_LENGTH = 100
_MAX_RESOURCE_TYPE_LENGTH = 100
_MAX_RESOURCE_ID_LENGTH = 255
_MIN_PAGE_LIMIT = 1
_MAX_PAGE_LIMIT = 100


class AuditLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_or_get_by_idempotency_key(
        self,
        entry: AuditLogEntry,
    ) -> tuple[AuditLogEntry, bool]:
        if entry.idempotency_key is None:
            self._session.add(entry)
            return entry, True

        statement = (
            insert(AuditLogEntry)
            .values(
                id=entry.id,
                tenant_id=entry.tenant_id,
                actor_type=entry.actor_type,
                actor_user_id=entry.actor_user_id,
                actor_role=entry.actor_role,
                source=entry.source,
                action=entry.action,
                resource_type=entry.resource_type,
                resource_id=entry.resource_id,
                metadata_version=entry.metadata_version,
                event_metadata=entry.event_metadata,
                idempotency_key=entry.idempotency_key,
                request_id=entry.request_id,
                correlation_id=entry.correlation_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    AuditLogEntry.idempotency_key,
                ],
                index_where=(AuditLogEntry.idempotency_key.is_not(None)),
            )
            .returning(AuditLogEntry.id)
        )

        inserted_id = self._session.scalar(statement)

        if inserted_id is not None:
            inserted = self._session.get(
                AuditLogEntry,
                inserted_id,
            )

            if inserted is None:
                raise AuditLogInvalidConfigurationError(
                    "The inserted audit log entry could not be loaded."
                )

            return inserted, True

        existing = self.get_by_idempotency_key(entry.idempotency_key)

        if existing is None:
            raise AuditLogInvalidConfigurationError(
                "The conflicting audit log entry could not be loaded."
            )

        return existing, False

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AuditLogEntry | None:
        return self._session.scalar(
            select(AuditLogEntry).where(AuditLogEntry.idempotency_key == idempotency_key)
        )

    def flush(self) -> None:
        self._session.flush()

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        audit_log_id: UUID,
    ) -> AuditLogRecord | None:
        if not isinstance(tenant_id, UUID):
            raise AuditLogInvalidConfigurationError("tenant_id must be a UUID.")

        if not isinstance(audit_log_id, UUID):
            raise AuditLogInvalidConfigurationError("audit_log_id must be a UUID.")

        entry = self._session.scalars(
            select(AuditLogEntry).where(
                and_(
                    AuditLogEntry.tenant_id == tenant_id,
                    AuditLogEntry.id == audit_log_id,
                )
            )
        ).one_or_none()

        if entry is None:
            return None

        return _to_audit_log_record(entry)

    def list_page_for_tenant(
        self,
        query: AuditLogQuery,
    ) -> AuditLogPage:
        normalized = _normalize_audit_log_query(query)

        conditions = [
            AuditLogEntry.tenant_id == normalized.tenant_id,
        ]

        if normalized.action is not None:
            conditions.append(AuditLogEntry.action == normalized.action)

        if normalized.resource_type is not None:
            conditions.append(AuditLogEntry.resource_type == normalized.resource_type)

        if normalized.resource_id is not None:
            conditions.append(AuditLogEntry.resource_id == normalized.resource_id)

        if normalized.cursor is not None:
            conditions.append(
                or_(
                    AuditLogEntry.recorded_at < normalized.cursor.recorded_at,
                    and_(
                        AuditLogEntry.recorded_at == normalized.cursor.recorded_at,
                        AuditLogEntry.id < normalized.cursor.audit_log_id,
                    ),
                )
            )

        entries = list(
            self._session.scalars(
                select(AuditLogEntry)
                .where(and_(*conditions))
                .order_by(
                    AuditLogEntry.recorded_at.desc(),
                    AuditLogEntry.id.desc(),
                )
                .limit(normalized.limit + 1)
            ).all()
        )

        has_next_page = len(entries) > normalized.limit
        page_entries = entries[: normalized.limit]
        items = tuple(_to_audit_log_record(entry) for entry in page_entries)

        next_cursor: AuditLogCursor | None = None
        if has_next_page and items:
            last_item = items[-1]
            next_cursor = AuditLogCursor(
                recorded_at=last_item.recorded_at,
                audit_log_id=last_item.audit_log_id,
            )

        return AuditLogPage(
            items=items,
            next_cursor=next_cursor,
        )


def _to_audit_log_record(
    entry: AuditLogEntry,
) -> AuditLogRecord:
    return AuditLogRecord(
        audit_log_id=entry.id,
        tenant_id=entry.tenant_id,
        actor_type=AuditActorType(entry.actor_type),
        actor_user_id=entry.actor_user_id,
        actor_role=entry.actor_role,
        source=AuditSource(entry.source),
        action=entry.action,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        metadata_version=entry.metadata_version,
        metadata=cast(JSONObject, deepcopy(entry.event_metadata)),
        idempotency_key=entry.idempotency_key,
        request_id=entry.request_id,
        correlation_id=entry.correlation_id,
        recorded_at=entry.recorded_at,
    )


def _normalize_audit_log_query(
    query: AuditLogQuery,
) -> AuditLogQuery:
    if not isinstance(query, AuditLogQuery):
        raise AuditLogInvalidConfigurationError("query must be an AuditLogQuery.")

    if not isinstance(query.tenant_id, UUID):
        raise AuditLogInvalidConfigurationError("tenant_id must be a UUID.")

    if type(query.limit) is not int:
        raise AuditLogInvalidConfigurationError("limit must be an integer from 1 through 100.")

    if query.limit < _MIN_PAGE_LIMIT or query.limit > _MAX_PAGE_LIMIT:
        raise AuditLogInvalidConfigurationError("limit must be an integer from 1 through 100.")

    cursor = query.cursor
    if cursor is not None:
        if not isinstance(cursor, AuditLogCursor):
            raise AuditLogInvalidConfigurationError("cursor must be an AuditLogCursor.")

        if cursor.recorded_at.tzinfo is None:
            raise AuditLogInvalidConfigurationError("cursor.recorded_at must be timezone-aware.")

        if not isinstance(cursor.audit_log_id, UUID):
            raise AuditLogInvalidConfigurationError("cursor.audit_log_id must be a UUID.")

    action = _normalize_optional_filter_string(
        query.action,
        field_name="action",
        maximum_length=_MAX_ACTION_LENGTH,
    )
    resource_type = _normalize_optional_filter_string(
        query.resource_type,
        field_name="resource_type",
        maximum_length=_MAX_RESOURCE_TYPE_LENGTH,
    )
    resource_id = _normalize_optional_filter_string(
        query.resource_id,
        field_name="resource_id",
        maximum_length=_MAX_RESOURCE_ID_LENGTH,
    )

    if resource_id is not None and resource_type is None:
        raise AuditLogInvalidConfigurationError("resource_id requires resource_type.")

    return AuditLogQuery(
        tenant_id=query.tenant_id,
        limit=query.limit,
        cursor=cursor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _normalize_optional_filter_string(
    value: str | None,
    *,
    field_name: str,
    maximum_length: int,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise AuditLogInvalidConfigurationError(f"{field_name} must be a string.")

    normalized = value.strip()

    if not normalized:
        raise AuditLogInvalidConfigurationError(f"{field_name} must not be blank.")

    if len(normalized) > maximum_length:
        raise AuditLogInvalidConfigurationError(
            f"{field_name} must contain at most {maximum_length} characters."
        )

    return normalized


__all__ = ["AuditLogRepository"]
