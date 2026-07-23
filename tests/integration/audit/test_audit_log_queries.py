from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from clinicops.audit.contracts import (
    AuditLogCursor,
    AuditLogQuery,
)
from clinicops.audit.enums import (
    AuditActorType,
    AuditSource,
)
from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)
from clinicops.audit.models import AuditLogEntry
from clinicops.audit.repositories.audit_log_repository import (
    AuditLogRepository,
)
from clinicops.db.session import get_engine


def _create_committed_tenants(
    count: int,
) -> list[UUID]:
    tenant_ids = [uuid4() for _ in range(count)]

    with Session(get_engine()) as session:
        for index, tenant_id in enumerate(tenant_ids):
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
                    "name": (f"Audit Query Clinic {index}-{uuid4().hex}"),
                },
            )

        session.commit()

    return tenant_ids


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


def _audit_entry(
    *,
    tenant_id: UUID,
    recorded_at: datetime,
    entry_id: UUID | None = None,
    action: str = "membership.role_changed",
    resource_type: str = "membership",
    resource_id: str | None = None,
) -> AuditLogEntry:
    return AuditLogEntry(
        id=(entry_id if entry_id is not None else uuid4()),
        tenant_id=tenant_id,
        actor_type=AuditActorType.SYSTEM.value,
        actor_user_id=None,
        actor_role=None,
        source=AuditSource.SYSTEM.value,
        action=action,
        resource_type=resource_type,
        resource_id=(resource_id if resource_id is not None else str(uuid4())),
        metadata_version=1,
        event_metadata={
            "source": "audit-query-test",
        },
        idempotency_key=None,
        request_id=None,
        correlation_id=f"correlation-{uuid4()}",
        recorded_at=recorded_at,
    )


def _persist_entries(
    *entries: AuditLogEntry,
) -> None:
    with Session(get_engine()) as session:
        session.add_all(entries)
        session.commit()


def test_get_by_id_for_tenant_enforces_tenant_scope() -> None:
    tenant_id, other_tenant_id = _create_committed_tenants(2)
    entry_id = uuid4()

    try:
        _persist_entries(
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=entry_id,
                recorded_at=datetime.now(UTC),
            )
        )

        with Session(get_engine()) as session:
            repository = AuditLogRepository(session)

            owned_record = repository.get_by_id_for_tenant(
                tenant_id=tenant_id,
                audit_log_id=entry_id,
            )
            foreign_record = repository.get_by_id_for_tenant(
                tenant_id=other_tenant_id,
                audit_log_id=entry_id,
            )

        assert owned_record is not None
        assert owned_record.audit_log_id == entry_id
        assert owned_record.tenant_id == tenant_id
        assert foreign_record is None
    finally:
        _delete_committed_tenant_data(
            tenant_id,
            other_tenant_id,
        )


def test_timeline_uses_stable_descending_order() -> None:
    tenant_id = _create_committed_tenants(1)[0]
    newest_timestamp = datetime.now(UTC)
    shared_timestamp = newest_timestamp - timedelta(minutes=1)
    oldest_timestamp = newest_timestamp - timedelta(minutes=2)

    newest_id = uuid4()
    shared_ids = [
        uuid4(),
        uuid4(),
    ]
    oldest_id = uuid4()

    try:
        _persist_entries(
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=oldest_id,
                recorded_at=oldest_timestamp,
            ),
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=shared_ids[0],
                recorded_at=shared_timestamp,
            ),
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=newest_id,
                recorded_at=newest_timestamp,
            ),
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=shared_ids[1],
                recorded_at=shared_timestamp,
            ),
        )

        with Session(get_engine()) as session:
            page = AuditLogRepository(session).list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=tenant_id,
                    limit=10,
                )
            )

        expected_shared_ids = sorted(
            shared_ids,
            reverse=True,
        )

        assert [item.audit_log_id for item in page.items] == [
            newest_id,
            *expected_shared_ids,
            oldest_id,
        ]
        assert page.next_cursor is None
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_cursor_pagination_uses_uuid_tie_breaker() -> None:
    tenant_id = _create_committed_tenants(1)[0]
    shared_timestamp = datetime.now(UTC)
    entry_ids = [uuid4() for _ in range(5)]

    try:
        _persist_entries(
            *[
                _audit_entry(
                    tenant_id=tenant_id,
                    entry_id=entry_id,
                    recorded_at=shared_timestamp,
                )
                for entry_id in entry_ids
            ]
        )

        with Session(get_engine()) as session:
            repository = AuditLogRepository(session)

            first_page = repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=tenant_id,
                    limit=2,
                )
            )

            assert first_page.next_cursor is not None

            second_page = repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=tenant_id,
                    limit=2,
                    cursor=(first_page.next_cursor),
                )
            )

            assert second_page.next_cursor is not None

            third_page = repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=tenant_id,
                    limit=2,
                    cursor=(second_page.next_cursor),
                )
            )

        combined_ids = [
            item.audit_log_id
            for page in (
                first_page,
                second_page,
                third_page,
            )
            for item in page.items
        ]

        assert combined_ids == sorted(
            entry_ids,
            reverse=True,
        )
        assert len(combined_ids) == len(set(combined_ids))

        assert first_page.next_cursor == (
            AuditLogCursor(
                recorded_at=(first_page.items[-1].recorded_at),
                audit_log_id=(first_page.items[-1].audit_log_id),
            )
        )
        assert second_page.next_cursor == (
            AuditLogCursor(
                recorded_at=(second_page.items[-1].recorded_at),
                audit_log_id=(second_page.items[-1].audit_log_id),
            )
        )
        assert third_page.next_cursor is None
        assert len(first_page.items) == 2
        assert len(second_page.items) == 2
        assert len(third_page.items) == 1
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_timeline_can_filter_by_action() -> None:
    tenant_id = _create_committed_tenants(1)[0]
    recorded_at = datetime.now(UTC)

    matching_ids = [
        uuid4(),
        uuid4(),
    ]
    other_id = uuid4()

    try:
        _persist_entries(
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=matching_ids[0],
                recorded_at=recorded_at,
                action="membership.role_changed",
            ),
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=other_id,
                recorded_at=recorded_at,
                action="membership.removed",
            ),
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=matching_ids[1],
                recorded_at=recorded_at,
                action="membership.role_changed",
            ),
        )

        with Session(get_engine()) as session:
            page = AuditLogRepository(session).list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=tenant_id,
                    limit=10,
                    action=("  membership.role_changed  "),
                )
            )

        assert {item.audit_log_id for item in page.items} == set(matching_ids)
        assert {item.action for item in page.items} == {
            "membership.role_changed",
        }
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_timeline_can_filter_by_resource() -> None:
    tenant_id = _create_committed_tenants(1)[0]
    recorded_at = datetime.now(UTC)
    target_resource_id = str(uuid4())

    target_id = uuid4()
    other_membership_id = uuid4()
    subscription_id = uuid4()

    try:
        _persist_entries(
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=target_id,
                recorded_at=recorded_at,
                resource_type="membership",
                resource_id=target_resource_id,
            ),
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=other_membership_id,
                recorded_at=recorded_at,
                resource_type="membership",
                resource_id=str(uuid4()),
            ),
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=subscription_id,
                recorded_at=recorded_at,
                resource_type="subscription",
                resource_id=str(uuid4()),
            ),
        )

        with Session(get_engine()) as session:
            repository = AuditLogRepository(session)

            resource_type_page = repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=tenant_id,
                    limit=10,
                    resource_type="membership",
                )
            )

            exact_resource_page = repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=tenant_id,
                    limit=10,
                    resource_type=("  membership  "),
                    resource_id=(f"  {target_resource_id}  "),
                )
            )

        assert {item.audit_log_id for item in resource_type_page.items} == {
            target_id,
            other_membership_id,
        }

        assert [item.audit_log_id for item in exact_resource_page.items] == [
            target_id,
        ]
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_timeline_does_not_return_another_tenants_entries() -> None:
    tenant_id, other_tenant_id = _create_committed_tenants(2)
    recorded_at = datetime.now(UTC)

    owned_id = uuid4()
    foreign_id = uuid4()

    try:
        _persist_entries(
            _audit_entry(
                tenant_id=tenant_id,
                entry_id=owned_id,
                recorded_at=recorded_at,
            ),
            _audit_entry(
                tenant_id=other_tenant_id,
                entry_id=foreign_id,
                recorded_at=recorded_at,
            ),
        )

        with Session(get_engine()) as session:
            page = AuditLogRepository(session).list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=tenant_id,
                    limit=10,
                )
            )

        assert [item.audit_log_id for item in page.items] == [
            owned_id,
        ]
        assert all(item.tenant_id == tenant_id for item in page.items)
    finally:
        _delete_committed_tenant_data(
            tenant_id,
            other_tenant_id,
        )


@pytest.mark.parametrize(
    "limit",
    [
        0,
        101,
        True,
    ],
)
def test_query_rejects_invalid_page_limit(
    limit: int,
) -> None:
    with Session(get_engine()) as session:
        repository = AuditLogRepository(session)

        with pytest.raises(
            AuditLogInvalidConfigurationError,
            match="limit",
        ):
            repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=uuid4(),
                    limit=limit,
                )
            )


def test_query_rejects_resource_id_without_resource_type() -> None:
    with Session(get_engine()) as session:
        repository = AuditLogRepository(session)

        with pytest.raises(
            AuditLogInvalidConfigurationError,
            match=("resource_id requires resource_type"),
        ):
            repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=uuid4(),
                    limit=10,
                    resource_id=str(uuid4()),
                )
            )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("action", " "),
        ("resource_type", ""),
        ("resource_id", "\t"),
    ],
)
def test_query_rejects_blank_filters(
    field_name: str,
    field_value: str,
) -> None:
    values: dict[str, str | None] = {
        "action": None,
        "resource_type": None,
        "resource_id": None,
    }
    values[field_name] = field_value

    if field_name == "resource_id":
        values["resource_type"] = "membership"

    with Session(get_engine()) as session:
        repository = AuditLogRepository(session)

        with pytest.raises(
            AuditLogInvalidConfigurationError,
            match=field_name,
        ):
            repository.list_page_for_tenant(
                AuditLogQuery(
                    tenant_id=uuid4(),
                    limit=10,
                    action=values["action"],
                    resource_type=(values["resource_type"]),
                    resource_id=(values["resource_id"]),
                )
            )
