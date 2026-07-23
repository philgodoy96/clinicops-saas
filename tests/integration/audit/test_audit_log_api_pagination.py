from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from clinicops.api.v1.audit_logs import (
    get_audit_log_tenant_context,
)
from clinicops.audit.enums import (
    AuditActorType,
    AuditSource,
)
from clinicops.audit.models import AuditLogEntry
from clinicops.authorization.permissions import (
    TenantPermission,
)
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.db.session import get_engine
from clinicops.main import create_app
from clinicops.tenancy.models import TenantRole


def _create_committed_tenant() -> UUID:
    tenant_id = uuid4()

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
                "name": (f"Audit Pagination Clinic {uuid4().hex}"),
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


def _entry(
    *,
    tenant_id: UUID,
    entry_id: UUID,
    recorded_at: datetime,
    action: str = "membership.role_changed",
    resource_type: str = "membership",
    resource_id: str | None = None,
) -> AuditLogEntry:
    return AuditLogEntry(
        id=entry_id,
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
            "test": "pagination",
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


def _authorized_context(
    tenant_id: UUID,
) -> AuthorizedTenantContext:
    return AuthorizedTenantContext(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid4(),
        role=TenantRole.OWNER,
        granted_permission=(TenantPermission.AUDIT_LOG_READ),
    )


@contextmanager
def _authorized_client(
    tenant_id: UUID,
) -> Iterator[TestClient]:
    application = create_app()
    context = _authorized_context(tenant_id)

    def override_tenant_context() -> AuthorizedTenantContext:
        return context

    application.dependency_overrides[get_audit_log_tenant_context] = override_tenant_context

    try:
        with TestClient(
            application,
            raise_server_exceptions=False,
        ) as client:
            yield client
    finally:
        application.dependency_overrides.clear()


def test_api_paginates_with_stable_timestamp_and_uuid_cursor() -> None:
    tenant_id = _create_committed_tenant()
    shared_timestamp = datetime(
        2026,
        7,
        23,
        12,
        0,
        tzinfo=UTC,
    )
    entry_ids = [uuid4() for _ in range(5)]

    try:
        _persist_entries(
            *[
                _entry(
                    tenant_id=tenant_id,
                    entry_id=entry_id,
                    recorded_at=shared_timestamp,
                )
                for entry_id in entry_ids
            ]
        )

        expected_ids = [
            str(entry_id)
            for entry_id in sorted(
                entry_ids,
                reverse=True,
            )
        ]

        with _authorized_client(tenant_id) as client:
            first_response = client.get(
                (f"/api/v1/tenants/{tenant_id}/audit-logs"),
                params={
                    "limit": 2,
                },
            )

            assert first_response.status_code == 200

            first_page = first_response.json()

            second_response = client.get(
                (f"/api/v1/tenants/{tenant_id}/audit-logs"),
                params={
                    "limit": 2,
                    "cursor": (first_page["next_cursor"]),
                },
            )

            assert second_response.status_code == 200

            second_page = second_response.json()

            third_response = client.get(
                (f"/api/v1/tenants/{tenant_id}/audit-logs"),
                params={
                    "limit": 2,
                    "cursor": (second_page["next_cursor"]),
                },
            )

            assert third_response.status_code == 200

            third_page = third_response.json()

        combined_ids = [
            item["id"]
            for page in (
                first_page,
                second_page,
                third_page,
            )
            for item in page["items"]
        ]

        assert combined_ids == expected_ids
        assert len(combined_ids) == len(set(combined_ids))

        assert len(first_page["items"]) == 2
        assert first_page["next_cursor"] is not None

        assert len(second_page["items"]) == 2
        assert second_page["next_cursor"] is not None

        assert len(third_page["items"]) == 1
        assert third_page["next_cursor"] is None
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_api_applies_action_and_resource_filters() -> None:
    tenant_id = _create_committed_tenant()
    newest = datetime.now(UTC)
    target_resource_id = str(uuid4())
    target_entry_id = uuid4()

    try:
        _persist_entries(
            _entry(
                tenant_id=tenant_id,
                entry_id=target_entry_id,
                recorded_at=newest,
                action="membership.role_changed",
                resource_type="membership",
                resource_id=target_resource_id,
            ),
            _entry(
                tenant_id=tenant_id,
                entry_id=uuid4(),
                recorded_at=(newest - timedelta(seconds=1)),
                action="membership.removed",
                resource_type="membership",
                resource_id=str(uuid4()),
            ),
            _entry(
                tenant_id=tenant_id,
                entry_id=uuid4(),
                recorded_at=(newest - timedelta(seconds=2)),
                action="billing.webhook.processed",
                resource_type=("billing_webhook_event"),
                resource_id=str(uuid4()),
            ),
        )

        with _authorized_client(tenant_id) as client:
            response = client.get(
                (f"/api/v1/tenants/{tenant_id}/audit-logs"),
                params={
                    "action": ("membership.role_changed"),
                    "resource_type": "membership",
                    "resource_id": target_resource_id,
                },
            )

        assert response.status_code == 200

        body = response.json()

        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == str(target_entry_id)
        assert body["items"][0]["action"] == "membership.role_changed"
        assert body["items"][0]["resource"] == {
            "type": "membership",
            "id": target_resource_id,
        }
        assert body["next_cursor"] is None
    finally:
        _delete_committed_tenant_data(tenant_id)


def test_api_paginates_mixed_domain_actions_without_duplicates() -> None:
    tenant_id = _create_committed_tenant()
    newest = datetime.now(UTC)
    membership_id = uuid4()
    invitation_id = uuid4()
    webhook_event_id = uuid4()
    entry_specs = [
        (
            membership_id,
            newest,
            "membership.role_changed",
            "membership",
            str(membership_id),
        ),
        (
            invitation_id,
            newest - timedelta(seconds=1),
            "invitation.created",
            "invitation",
            str(invitation_id),
        ),
        (
            webhook_event_id,
            newest - timedelta(seconds=2),
            "billing.webhook.processed",
            "billing_webhook_event",
            str(webhook_event_id),
        ),
        (
            uuid4(),
            newest - timedelta(seconds=3),
            "membership.removed",
            "membership",
            str(uuid4()),
        ),
        (
            uuid4(),
            newest - timedelta(seconds=4),
            "billing.subscription.cancelled",
            "subscription",
            str(uuid4()),
        ),
    ]

    try:
        _persist_entries(
            *[
                _entry(
                    tenant_id=tenant_id,
                    entry_id=entry_id,
                    recorded_at=recorded_at,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
                for (
                    entry_id,
                    recorded_at,
                    action,
                    resource_type,
                    resource_id,
                ) in entry_specs
            ]
        )

        expected_ids = [str(entry_id) for entry_id, *_ in entry_specs]
        collected_ids: list[str] = []
        collected_actions: list[str] = []
        collected_resource_types: list[str] = []
        cursor: str | None = None

        with _authorized_client(tenant_id) as client:
            while True:
                params: dict[str, str | int] = {"limit": 2}
                if cursor is not None:
                    params["cursor"] = cursor

                response = client.get(
                    (f"/api/v1/tenants/{tenant_id}/audit-logs"),
                    params=params,
                )

                assert response.status_code == 200

                page = response.json()
                page_items = page["items"]

                assert len(page_items) <= 2

                for item in page_items:
                    collected_ids.append(item["id"])
                    collected_actions.append(item["action"])
                    collected_resource_types.append(item["resource"]["type"])
                    assert "idempotency_key" not in item

                cursor = page["next_cursor"]

                if cursor is None:
                    break

        assert collected_ids == expected_ids
        assert len(collected_ids) == len(set(collected_ids))
        assert set(collected_actions) >= {
            "membership.role_changed",
            "invitation.created",
            "billing.webhook.processed",
        }
        assert set(collected_resource_types) >= {
            "membership",
            "invitation",
            "billing_webhook_event",
        }
        assert cursor is None
    finally:
        _delete_committed_tenant_data(tenant_id)
