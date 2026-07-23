from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from clinicops.api.errors import PROBLEM_MEDIA_TYPE
from clinicops.api.v1.audit_logs import (
    get_audit_log_tenant_context,
)
from clinicops.authorization.exceptions import (
    TenantPermissionDeniedError,
)
from clinicops.authorization.permissions import (
    TenantPermission,
)
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.main import create_app
from clinicops.tenancy.models import TenantRole


def _authorized_context(
    tenant_id: UUID,
) -> AuthorizedTenantContext:
    return AuthorizedTenantContext(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid4(),
        role=TenantRole.ADMIN,
        granted_permission=(TenantPermission.AUDIT_LOG_READ),
    )


@contextmanager
def _client_with_context_override(
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


def test_unauthenticated_request_is_rejected() -> None:
    tenant_id = uuid4()

    with TestClient(
        create_app(),
        raise_server_exceptions=False,
    ) as client:
        response = client.get(f"/api/v1/tenants/{tenant_id}/audit-logs")

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE


def test_permission_denial_returns_problem_details() -> None:
    tenant_id = uuid4()
    application = create_app()

    def deny_tenant_context() -> AuthorizedTenantContext:
        raise TenantPermissionDeniedError()

    application.dependency_overrides[get_audit_log_tenant_context] = deny_tenant_context

    try:
        with TestClient(
            application,
            raise_server_exceptions=False,
        ) as client:
            response = client.get(f"/api/v1/tenants/{tenant_id}/audit-logs")
    finally:
        application.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE

    body = response.json()

    assert body["code"] == ("tenant_permission_denied")
    assert body["request_id"]
    assert body["correlation_id"]


def test_invalid_cursor_returns_bad_request_problem() -> None:
    tenant_id = uuid4()

    with _client_with_context_override(tenant_id) as client:
        response = client.get(
            (f"/api/v1/tenants/{tenant_id}/audit-logs"),
            params={
                "cursor": "not*valid",
            },
        )

    assert response.status_code == 400
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE

    body = response.json()

    assert body["code"] == ("audit_log_invalid_configuration")
    assert body["detail"] == ("The audit log request is invalid.")
    assert body["request_id"]
    assert body["correlation_id"]
    assert "URL-safe Base64" not in response.text


def test_resource_id_without_type_returns_bad_request() -> None:
    tenant_id = uuid4()

    with _client_with_context_override(tenant_id) as client:
        response = client.get(
            (f"/api/v1/tenants/{tenant_id}/audit-logs"),
            params={
                "resource_id": str(uuid4()),
            },
        )

    assert response.status_code == 400
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["code"] == ("audit_log_invalid_configuration")


@pytest.mark.parametrize(
    "limit",
    [
        0,
        101,
    ],
)
def test_invalid_limit_uses_request_validation_problem(
    limit: int,
) -> None:
    tenant_id = uuid4()

    with _client_with_context_override(tenant_id) as client:
        response = client.get(
            (f"/api/v1/tenants/{tenant_id}/audit-logs"),
            params={
                "limit": limit,
            },
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["code"] == ("request_validation_error")
