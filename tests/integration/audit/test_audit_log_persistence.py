from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.audit.enums import (
    AuditActorType,
    AuditSource,
)
from clinicops.audit.models import AuditLogEntry
from clinicops.db.session import get_engine


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _insert_tenant(
    session: Session,
) -> UUID:
    tenant_id = uuid4()
    suffix = uuid4().hex

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
            "name": f"Audit Test Clinic {suffix}",
        },
    )
    session.flush()

    return tenant_id


def _system_entry(
    *,
    tenant_id: UUID,
    idempotency_key: str | None = None,
    action: str = "tenant.created",
    resource_id: str | None = None,
) -> AuditLogEntry:
    return AuditLogEntry(
        tenant_id=tenant_id,
        actor_type=AuditActorType.SYSTEM.value,
        actor_user_id=None,
        actor_role=None,
        source=AuditSource.SYSTEM.value,
        action=action,
        resource_type="tenant",
        resource_id=(resource_id if resource_id is not None else str(tenant_id)),
        metadata_version=1,
        event_metadata={
            "origin": "integration-test",
            "nested": {
                "enabled": True,
                "sequence": [1, 2, 3],
            },
        },
        idempotency_key=idempotency_key,
        request_id=None,
        correlation_id=f"correlation-{uuid4()}",
    )


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
            {"tenant_id": tenant_id},
        )
        session.commit()


def test_audit_log_schema_contains_expected_indexes() -> None:
    inspector = inspect(get_engine())

    columns = {column["name"]: column for column in inspector.get_columns("audit_log_entries")}
    indexes = {index["name"]: index for index in inspector.get_indexes("audit_log_entries")}

    assert set(columns) == {
        "id",
        "tenant_id",
        "actor_type",
        "actor_user_id",
        "actor_role",
        "source",
        "action",
        "resource_type",
        "resource_id",
        "metadata_version",
        "metadata",
        "idempotency_key",
        "request_id",
        "correlation_id",
        "recorded_at",
    }

    assert {
        "ix_audit_log_entries_tenant_timeline",
        "ix_audit_log_entries_tenant_action_timeline",
        "ix_audit_log_entries_tenant_resource_timeline",
        "uq_audit_log_entries_idempotency_key",
    }.issubset(indexes)

    assert indexes["uq_audit_log_entries_idempotency_key"]["unique"] is True


def test_audit_log_persists_jsonb_and_database_timestamp() -> None:
    engine = get_engine()
    tenant_id: UUID | None = None

    try:
        with Session(engine) as session:
            tenant_id = _insert_tenant(session)
            entry = _system_entry(
                tenant_id=tenant_id,
                idempotency_key=(f"audit-persistence:{uuid4()}"),
            )

            session.add(entry)
            session.commit()

            entry_id = entry.id

        with Session(engine) as verification_session:
            persisted = verification_session.get(
                AuditLogEntry,
                entry_id,
            )

            assert persisted is not None
            assert persisted.tenant_id == tenant_id
            assert persisted.actor_type == AuditActorType.SYSTEM.value
            assert persisted.source == AuditSource.SYSTEM.value
            assert persisted.action == "tenant.created"
            assert persisted.resource_type == "tenant"
            assert persisted.resource_id == str(tenant_id)
            assert persisted.metadata_version == 1
            assert persisted.event_metadata == {
                "origin": "integration-test",
                "nested": {
                    "enabled": True,
                    "sequence": [1, 2, 3],
                },
            }
            assert persisted.recorded_at is not None
            assert persisted.recorded_at.tzinfo is not None
    finally:
        if tenant_id is not None:
            _delete_committed_tenant_data(tenant_id)


def test_user_actor_requires_actor_user_id(
    db_session: Session,
) -> None:
    tenant_id = _insert_tenant(db_session)
    entry = _system_entry(tenant_id=tenant_id)

    entry.actor_type = AuditActorType.USER.value

    db_session.add(entry)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_system_actor_rejects_actor_role(
    db_session: Session,
) -> None:
    tenant_id = _insert_tenant(db_session)
    entry = _system_entry(tenant_id=tenant_id)

    entry.actor_role = "OWNER"

    db_session.add(entry)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_database_rejects_unknown_source(
    db_session: Session,
) -> None:
    tenant_id = _insert_tenant(db_session)
    entry = _system_entry(tenant_id=tenant_id)

    entry.source = "unknown"

    db_session.add(entry)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_database_rejects_blank_required_fields(
    db_session: Session,
) -> None:
    tenant_id = _insert_tenant(db_session)
    entry = _system_entry(tenant_id=tenant_id)

    entry.action = " "

    db_session.add(entry)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_non_null_idempotency_key_is_unique(
    db_session: Session,
) -> None:
    tenant_id = _insert_tenant(db_session)
    idempotency_key = f"audit-unique:{uuid4()}"

    first_entry = _system_entry(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        resource_id=f"first-{uuid4()}",
    )
    second_entry = _system_entry(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        resource_id=f"second-{uuid4()}",
    )

    db_session.add(first_entry)
    db_session.flush()

    db_session.add(second_entry)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_multiple_null_idempotency_keys_are_allowed(
    db_session: Session,
) -> None:
    tenant_id = _insert_tenant(db_session)

    first_entry = _system_entry(
        tenant_id=tenant_id,
        resource_id=f"first-{uuid4()}",
    )
    second_entry = _system_entry(
        tenant_id=tenant_id,
        resource_id=f"second-{uuid4()}",
    )

    db_session.add_all(
        [
            first_entry,
            second_entry,
        ]
    )
    db_session.flush()

    assert first_entry.id != second_entry.id
    assert first_entry.idempotency_key is None
    assert second_entry.idempotency_key is None
