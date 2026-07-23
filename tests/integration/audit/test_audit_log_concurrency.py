from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.audit.contracts import (
    AuditActor,
    AuditLogQuery,
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


@dataclass(frozen=True, slots=True)
class ConcurrentRecordOutcome:
    audit_log_id: UUID | None
    created: bool | None
    conflict: bool


def _create_committed_tenant(
    *,
    name: str | None = None,
) -> tuple[UUID, str]:
    tenant_id = uuid4()
    tenant_name = name if name is not None else f"Audit Concurrency Clinic {uuid4().hex}"

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
                "name": tenant_name,
            },
        )
        session.commit()

    return tenant_id, tenant_name


def _delete_committed_tenant_data(
    *tenant_ids: UUID,
) -> None:
    if not tenant_ids:
        return

    with Session(get_engine()) as session:
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id.in_(tenant_ids)))

        for tenant_id in tenant_ids:
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
    *,
    tenant_id: UUID,
    idempotency_key: str,
    action: str = "tenant.settings_changed",
    request_id: str,
    correlation_id: str,
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


def _record_and_commit(
    command: RecordAuditLogCommand,
    barrier: Barrier,
) -> ConcurrentRecordOutcome:
    with Session(get_engine()) as session:
        barrier.wait(timeout=10)

        result = RecordAuditLogService(AuditLogRepository(session)).execute(command)

        session.commit()

        return ConcurrentRecordOutcome(
            audit_log_id=result.audit_log_id,
            created=result.created,
            conflict=False,
        )


def _record_conflicting_and_commit(
    command: RecordAuditLogCommand,
    barrier: Barrier,
) -> ConcurrentRecordOutcome:
    with Session(get_engine()) as session:
        barrier.wait(timeout=10)

        try:
            result = RecordAuditLogService(AuditLogRepository(session)).execute(command)

            session.commit()

            return ConcurrentRecordOutcome(
                audit_log_id=result.audit_log_id,
                created=result.created,
                conflict=False,
            )
        except AuditLogIdempotencyConflictError:
            session.rollback()

            return ConcurrentRecordOutcome(
                audit_log_id=None,
                created=None,
                conflict=True,
            )


def test_concurrent_equivalent_recording_creates_one_entry() -> None:
    tenant_id, _ = _create_committed_tenant()
    idempotency_key = f"audit-concurrent-equivalent:{uuid4()}"
    barrier = Barrier(2)

    commands = [
        _command(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            request_id="request-concurrent-1",
            correlation_id="correlation-concurrent-1",
        ),
        _command(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            request_id="request-concurrent-2",
            correlation_id="correlation-concurrent-2",
        ),
    ]

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _record_and_commit,
                    command,
                    barrier,
                )
                for command in commands
            ]

            outcomes = [future.result(timeout=20) for future in futures]

        assert {outcome.audit_log_id for outcome in outcomes} == {
            outcomes[0].audit_log_id,
        }
        assert sorted(outcome.created for outcome in outcomes if outcome.created is not None) == [
            False,
            True,
        ]
        assert all(outcome.conflict is False for outcome in outcomes)

        with Session(get_engine()) as session:
            entries = session.scalars(
                select(AuditLogEntry).where(AuditLogEntry.idempotency_key == idempotency_key)
            ).all()

        assert len(entries) == 1
        assert entries[0].id == (outcomes[0].audit_log_id)
        assert entries[0].request_id in {
            "request-concurrent-1",
            "request-concurrent-2",
        }
        assert entries[0].correlation_id in {
            "correlation-concurrent-1",
            "correlation-concurrent-2",
        }
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_concurrent_conflicting_recording_has_one_winner() -> None:
    tenant_id, _ = _create_committed_tenant()
    idempotency_key = f"audit-concurrent-conflict:{uuid4()}"
    barrier = Barrier(2)

    commands = [
        _command(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            action="tenant.settings_changed",
            request_id="request-settings",
            correlation_id="correlation-settings",
        ),
        _command(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            action="tenant.deleted",
            request_id="request-deleted",
            correlation_id="correlation-deleted",
        ),
    ]

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _record_conflicting_and_commit,
                    command,
                    barrier,
                )
                for command in commands
            ]

            outcomes = [future.result(timeout=20) for future in futures]

        successful = [outcome for outcome in outcomes if outcome.conflict is False]
        conflicting = [outcome for outcome in outcomes if outcome.conflict is True]

        assert len(successful) == 1
        assert successful[0].created is True
        assert successful[0].audit_log_id is not None

        assert len(conflicting) == 1
        assert conflicting[0].audit_log_id is None
        assert conflicting[0].created is None

        with Session(get_engine()) as session:
            entries = session.scalars(
                select(AuditLogEntry).where(AuditLogEntry.idempotency_key == idempotency_key)
            ).all()

        assert len(entries) == 1
        assert entries[0].id == (successful[0].audit_log_id)
        assert entries[0].action in {
            "tenant.settings_changed",
            "tenant.deleted",
        }
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_domain_mutation_and_audit_entry_roll_back_together() -> None:
    original_name = f"Audit Rollback Clinic {uuid4().hex}"
    tenant_id, _ = _create_committed_tenant(name=original_name)
    idempotency_key = f"audit-domain-rollback:{uuid4()}"

    try:
        with Session(get_engine()) as session:
            session.execute(
                text(
                    """
                    UPDATE tenants
                    SET name = :new_name
                    WHERE id = :tenant_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "new_name": ("Uncommitted Audit Clinic"),
                },
            )

            result = RecordAuditLogService(AuditLogRepository(session)).execute(
                _command(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    request_id="request-rollback",
                    correlation_id=("correlation-rollback"),
                )
            )

            audit_log_id = result.audit_log_id

            session.rollback()

        with Session(get_engine()) as session:
            persisted_name = session.scalar(
                text(
                    """
                    SELECT name
                    FROM tenants
                    WHERE id = :tenant_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                },
            )
            persisted_audit = session.get(
                AuditLogEntry,
                audit_log_id,
            )

        assert persisted_name == original_name
        assert persisted_audit is None
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_audit_persistence_failure_prevents_domain_commit() -> None:
    original_name = f"Audit Failure Clinic {uuid4().hex}"
    tenant_id, _ = _create_committed_tenant(name=original_name)
    nonexistent_tenant_id = uuid4()
    idempotency_key = f"audit-foreign-key-failure:{uuid4()}"

    try:
        with Session(get_engine()) as session:
            session.execute(
                text(
                    """
                    UPDATE tenants
                    SET name = :new_name
                    WHERE id = :tenant_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "new_name": ("Must Not Be Committed"),
                },
            )

            with pytest.raises(IntegrityError):
                RecordAuditLogService(AuditLogRepository(session)).execute(
                    _command(
                        tenant_id=(nonexistent_tenant_id),
                        idempotency_key=(idempotency_key),
                        request_id=("request-failure"),
                        correlation_id=("correlation-failure"),
                    )
                )

            session.rollback()

        with Session(get_engine()) as session:
            persisted_name = session.scalar(
                text(
                    """
                    SELECT name
                    FROM tenants
                    WHERE id = :tenant_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                },
            )
            audit_count = session.scalar(
                select(func.count())
                .select_from(AuditLogEntry)
                .where(AuditLogEntry.idempotency_key == idempotency_key)
            )

        assert persisted_name == original_name
        assert audit_count == 0
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_tenant_scoped_queries_do_not_expose_foreign_entries() -> None:
    tenant_id, _ = _create_committed_tenant()
    other_tenant_id, _ = _create_committed_tenant()

    try:
        with Session(get_engine()) as session:
            result = RecordAuditLogService(AuditLogRepository(session)).execute(
                _command(
                    tenant_id=tenant_id,
                    idempotency_key=(f"audit-tenant-scope:{uuid4()}"),
                    request_id="request-scope",
                    correlation_id=("correlation-scope"),
                )
            )
            session.commit()

        with Session(get_engine()) as session:
            repository = AuditLogRepository(session)

            foreign_lookup = repository.get_by_id_for_tenant(
                tenant_id=other_tenant_id,
                audit_log_id=(result.audit_log_id),
            )
            foreign_page = repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=other_tenant_id,
                    limit=100,
                )
            )

        assert foreign_lookup is None
        assert foreign_page.items == ()
        assert foreign_page.next_cursor is None
    finally:
        _delete_committed_tenant_data(
            tenant_id,
            other_tenant_id,
        )


def test_repository_exposes_no_mutation_or_transaction_methods() -> None:
    forbidden_methods = {
        "update",
        "delete",
        "remove",
        "save",
        "commit",
        "rollback",
        "get_all",
        "list_all",
    }

    for method_name in forbidden_methods:
        assert not hasattr(
            AuditLogRepository,
            method_name,
        )
