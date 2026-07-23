from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from clinicops.audit.contracts import (
    AuditActor,
    RecordAuditLogCommand,
)
from clinicops.audit.enums import AuditSource
from clinicops.audit.exceptions import (
    AuditLogIdempotencyConflictError,
)
from clinicops.audit.models import AuditLogEntry
from clinicops.audit.repositories.audit_log_repository import (
    AuditLogRepository,
)
from clinicops.audit.services.record_audit_log import (
    RecordAuditLogService,
)
from clinicops.db.session import get_engine


def _create_committed_tenant() -> UUID:
    tenant_id = uuid4()
    suffix = uuid4().hex

    with Session(get_engine()) as session:
        session.execute(
            text(
                """
                INSERT INTO tenants (
                    id,
                    name
                )
                VALUES (
                    :tenant_id,
                    :name
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "name": f"Audit Recording {suffix}",
            },
        )
        session.commit()

    return tenant_id


def _delete_committed_tenant_data(
    tenant_id: UUID,
) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id))
        session.execute(
            text(
                """
                DELETE FROM tenants
                WHERE id = :tenant_id
                """
            ),
            {
                "tenant_id": tenant_id,
            },
        )
        session.commit()


def _command(
    tenant_id: UUID,
    *,
    idempotency_key: str | None,
    action: str = "tenant.settings_changed",
    request_id: str | None = "request-original",
    correlation_id: str = "correlation-original",
) -> RecordAuditLogCommand:
    return RecordAuditLogCommand(
        tenant_id=tenant_id,
        actor=AuditActor.system(),
        source=AuditSource.SYSTEM,
        action=action,
        resource_type="tenant",
        resource_id=str(tenant_id),
        correlation_id=correlation_id,
        metadata_version=1,
        metadata={
            "new_timezone": "America/New_York",
            "previous_timezone": "UTC",
        },
        idempotency_key=idempotency_key,
        request_id=request_id,
    )


def _service(
    session: Session,
) -> RecordAuditLogService:
    return RecordAuditLogService(AuditLogRepository(session))


def test_caller_commit_persists_recorded_audit_log() -> None:
    tenant_id = _create_committed_tenant()

    try:
        with Session(get_engine()) as session:
            result = _service(session).execute(
                _command(
                    tenant_id,
                    idempotency_key=(f"audit-recording:{uuid4()}"),
                )
            )

            session.commit()
            audit_log_id = result.audit_log_id

        with Session(get_engine()) as session:
            persisted = session.get(
                AuditLogEntry,
                audit_log_id,
            )

            assert persisted is not None
            assert persisted.tenant_id == tenant_id
            assert persisted.recorded_at is not None
            assert persisted.recorded_at.tzinfo is not None
            assert result.created is True
            assert persisted.event_metadata == {
                "new_timezone": ("America/New_York"),
                "previous_timezone": "UTC",
            }
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_service_does_not_commit_caller_transaction() -> None:
    tenant_id = _create_committed_tenant()

    try:
        session = Session(get_engine())

        try:
            result = _service(session).execute(
                _command(
                    tenant_id,
                    idempotency_key=(f"audit-uncommitted:{uuid4()}"),
                )
            )

            with Session(get_engine()) as independent:
                persisted = independent.get(
                    AuditLogEntry,
                    result.audit_log_id,
                )

            assert persisted is None
        finally:
            session.rollback()
            session.close()
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_caller_rollback_removes_audit_entry() -> None:
    tenant_id = _create_committed_tenant()

    try:
        with Session(get_engine()) as session:
            result = _service(session).execute(
                _command(
                    tenant_id,
                    idempotency_key=None,
                )
            )
            audit_log_id = result.audit_log_id

            session.rollback()

        with Session(get_engine()) as session:
            persisted = session.get(
                AuditLogEntry,
                audit_log_id,
            )

            assert persisted is None
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_equivalent_replay_reuses_existing_entry() -> None:
    tenant_id = _create_committed_tenant()
    idempotency_key = f"audit-replay:{uuid4()}"

    try:
        with Session(get_engine()) as session:
            first_result = _service(session).execute(
                _command(
                    tenant_id,
                    idempotency_key=idempotency_key,
                )
            )
            session.commit()

        with Session(get_engine()) as session:
            second_result = _service(session).execute(
                _command(
                    tenant_id,
                    idempotency_key=idempotency_key,
                    request_id="request-replay",
                    correlation_id="correlation-replay",
                )
            )
            session.commit()

        assert first_result.created is True
        assert second_result.created is False
        assert second_result.audit_log_id == first_result.audit_log_id

        with Session(get_engine()) as session:
            entries = session.scalars(
                select(AuditLogEntry).where(AuditLogEntry.idempotency_key == idempotency_key)
            ).all()

            assert len(entries) == 1
            assert entries[0].request_id == "request-original"
            assert entries[0].correlation_id == "correlation-original"
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_conflicting_replay_raises_semantic_conflict() -> None:
    tenant_id = _create_committed_tenant()
    idempotency_key = f"audit-conflict:{uuid4()}"

    try:
        with Session(get_engine()) as session:
            _service(session).execute(
                _command(
                    tenant_id,
                    idempotency_key=idempotency_key,
                )
            )
            session.commit()

        with Session(get_engine()) as session:
            with pytest.raises(
                AuditLogIdempotencyConflictError,
            ):
                _service(session).execute(
                    _command(
                        tenant_id,
                        idempotency_key=idempotency_key,
                        action="tenant.deleted",
                    )
                )

            count = session.scalar(
                select(func.count())
                .select_from(AuditLogEntry)
                .where(AuditLogEntry.idempotency_key == idempotency_key)
            )

            assert count == 1

            session.commit()
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_missing_idempotency_key_appends_entries() -> None:
    tenant_id = _create_committed_tenant()

    try:
        with Session(get_engine()) as session:
            first_result = _service(session).execute(
                _command(
                    tenant_id,
                    idempotency_key=None,
                )
            )
            second_result = _service(session).execute(
                _command(
                    tenant_id,
                    idempotency_key=None,
                )
            )

            session.commit()

        assert first_result.created is True
        assert second_result.created is True
        assert first_result.audit_log_id != second_result.audit_log_id

        with Session(get_engine()) as session:
            count = session.scalar(
                select(func.count())
                .select_from(AuditLogEntry)
                .where(AuditLogEntry.tenant_id == tenant_id)
            )

            assert count == 2
    finally:
        _delete_committed_tenant_data(tenant_id)
