from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
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
                "name": (f"Audit API Clinic {uuid4().hex}"),
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


def _persist_audit_entry(
    tenant_id: UUID,
) -> AuditLogEntry:
    entry = AuditLogEntry(
        id=uuid4(),
        tenant_id=tenant_id,
        actor_type=AuditActorType.SYSTEM.value,
        actor_user_id=None,
        actor_role=None,
        source=AuditSource.WORKER.value,
        action="billing.webhook.processed",
        resource_type="billing_webhook_event",
        resource_id=str(uuid4()),
        metadata_version=1,
        event_metadata={
            "event_type": "subscription.updated",
            "processing_outcome": "processed",
        },
        idempotency_key=(f"internal-audit-key:{uuid4()}"),
        request_id="origin-request-123",
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

    with Session(
        get_engine(),
        expire_on_commit=False,
    ) as session:
        session.add(entry)
        session.commit()
        session.expunge(entry)

    return entry


def _authorized_context(
    tenant_id: UUID,
    role: TenantRole,
) -> AuthorizedTenantContext:
    return AuthorizedTenantContext(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid4(),
        role=role,
        granted_permission=(TenantPermission.AUDIT_LOG_READ),
    )


@contextmanager
def _authorized_client(
    tenant_id: UUID,
    role: TenantRole,
) -> Iterator[TestClient]:
    application = create_app()
    context = _authorized_context(
        tenant_id,
        role,
    )

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


def test_audit_log_route_is_registered() -> None:
    paths = create_app().openapi()["paths"]

    assert "/api/v1/tenants/{tenant_id}/audit-logs" in paths


@pytest.mark.parametrize(
    "role",
    [
        TenantRole.OWNER,
        TenantRole.ADMIN,
    ],
)
def test_owner_and_admin_can_read_tenant_audit_logs(
    role: TenantRole,
) -> None:
    tenant_id = _create_committed_tenant()

    try:
        entry = _persist_audit_entry(tenant_id)

        with _authorized_client(
            tenant_id,
            role,
        ) as client:
            response = client.get(f"/api/v1/tenants/{tenant_id}/audit-logs")

        assert response.status_code == 200

        body = response.json()

        assert body["next_cursor"] is None
        assert len(body["items"]) == 1

        item = body["items"][0]

        assert item["id"] == str(entry.id)
        assert item["tenant_id"] == str(tenant_id)

        assert item["actor"] == {
            "type": "system",
            "user_id": None,
            "role": None,
        }
        assert item["source"] == "worker"
        assert item["action"] == "billing.webhook.processed"
        assert item["resource"] == {
            "type": "billing_webhook_event",
            "id": entry.resource_id,
        }
        assert item["metadata_version"] == 1
        assert item["metadata"] == {
            "event_type": "subscription.updated",
            "processing_outcome": "processed",
        }
        assert item["request_id"] == "origin-request-123"
        assert item["correlation_id"] == "correlation-123"

        assert "idempotency_key" not in item
        assert "idempotency_key" not in body
    finally:
        _delete_committed_tenant_data(tenant_id)
