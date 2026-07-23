from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)
from clinicops.audit.models import AuditLogEntry


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


__all__ = ["AuditLogRepository"]
