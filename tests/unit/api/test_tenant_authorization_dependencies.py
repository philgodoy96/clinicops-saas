from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from clinicops.api.dependencies import get_database_session
from clinicops.api.errors import (
    PROBLEM_MEDIA_TYPE,
    register_exception_handlers,
)
from clinicops.api.middleware.request_context import (
    RequestContextMiddleware,
)
from clinicops.api.v1.authentication.dependencies import (
    get_authenticated_principal,
)
from clinicops.api.v1.tenants.dependencies import (
    TenantContextDependency,
    get_require_tenant_permission_service,
    get_resolve_tenant_context_service,
    get_tenant_context,
    require_tenant_permission,
)
from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
)
from clinicops.authorization.exceptions import (
    TenantDisabledError,
    TenantMembershipDisabledError,
    TenantMembershipNotFoundError,
    TenantNotFoundError,
    TenantPermissionDeniedError,
)
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
    RequireTenantPermissionCommand,
    RequireTenantPermissionService,
)
from clinicops.authorization.services.resolve_tenant_context import (
    ResolveTenantContextCommand,
    ResolveTenantContextService,
    TenantContext,
)
from clinicops.tenancy.models import TenantRole

FIXED_NOW = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)

TenantReadAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(
        require_tenant_permission(
            TenantPermission.TENANT_READ,
        )
    ),
]


class RecordingSession:
    """Track accidental commits during tenant authorization."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeResolveTenantContextService:
    """Return one deterministic tenant context or failure."""

    def __init__(
        self,
        result: TenantContext | Exception,
    ) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: ResolveTenantContextCommand | None = None

    def execute(
        self,
        session: Session,
        command: ResolveTenantContextCommand,
    ) -> TenantContext:
        self.call_count += 1
        self.received_session = session
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


class FakeRequireTenantPermissionService:
    """Return one deterministic authorization result or failure."""

    def __init__(
        self,
        result: AuthorizedTenantContext | Exception,
    ) -> None:
        self.result = result
        self.call_count = 0
        self.received_command: RequireTenantPermissionCommand | None = None

    def execute(
        self,
        command: RequireTenantPermissionCommand,
    ) -> AuthorizedTenantContext:
        self.call_count += 1
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


def build_principal() -> AuthenticatedPrincipal:
    """Build one trusted global principal."""

    return AuthenticatedPrincipal(
        user_id=uuid4(),
        session_id=uuid4(),
        access_token_id=uuid4(),
        authenticated_at=FIXED_NOW - timedelta(minutes=5),
        access_token_expires_at=FIXED_NOW + timedelta(minutes=10),
        session_expires_at=FIXED_NOW + timedelta(days=29),
    )


def build_tenant_context(
    principal: AuthenticatedPrincipal,
    *,
    tenant_id: UUID | None = None,
    role: TenantRole = TenantRole.ADMIN,
) -> TenantContext:
    """Build one trusted tenant context."""

    return TenantContext(
        user_id=principal.user_id,
        session_id=principal.session_id,
        tenant_id=tenant_id or uuid4(),
        membership_id=uuid4(),
        role=role,
    )


def build_authorized_context(
    tenant_context: TenantContext,
    permission: TenantPermission,
) -> AuthorizedTenantContext:
    """Build one permission-proven tenant context."""

    return AuthorizedTenantContext(
        user_id=tenant_context.user_id,
        session_id=tenant_context.session_id,
        tenant_id=tenant_context.tenant_id,
        membership_id=tenant_context.membership_id,
        role=tenant_context.role,
        granted_permission=permission,
    )


def test_tenant_authorization_dependencies_build_services() -> None:
    assert isinstance(
        get_resolve_tenant_context_service(),
        ResolveTenantContextService,
    )
    assert isinstance(
        get_require_tenant_permission_service(),
        RequireTenantPermissionService,
    )


def test_tenant_context_dependency_translates_trusted_inputs_without_commit() -> None:
    principal = build_principal()
    tenant_id = uuid4()
    tenant_context = build_tenant_context(
        principal,
        tenant_id=tenant_id,
    )
    recording_session = RecordingSession()
    fake_service = FakeResolveTenantContextService(tenant_context)

    resolved_context = get_tenant_context(
        tenant_id=tenant_id,
        principal=principal,
        session=cast(Session, recording_session),
        service=cast(ResolveTenantContextService, fake_service),
    )

    assert resolved_context is tenant_context
    assert fake_service.call_count == 1
    assert fake_service.received_session is cast(Session, recording_session)
    assert fake_service.received_command == ResolveTenantContextCommand(
        principal=principal,
        tenant_id=tenant_id,
    )
    assert recording_session.commit_count == 0


def test_permission_dependency_captures_required_permission() -> None:
    principal = build_principal()
    tenant_context = build_tenant_context(principal)
    required_permission = TenantPermission.MEMBER_READ
    authorized_context = build_authorized_context(
        tenant_context,
        required_permission,
    )
    fake_service = FakeRequireTenantPermissionService(authorized_context)
    dependency = require_tenant_permission(required_permission)

    resolved_context = dependency(
        tenant_context=tenant_context,
        service=cast(
            RequireTenantPermissionService,
            fake_service,
        ),
    )

    assert resolved_context is authorized_context
    assert fake_service.call_count == 1
    assert fake_service.received_command == (
        RequireTenantPermissionCommand(
            tenant_context=tenant_context,
            permission=required_permission,
        )
    )


@pytest.mark.parametrize(
    "exception",
    [
        TenantNotFoundError(),
        TenantDisabledError(),
        TenantMembershipNotFoundError(),
        TenantMembershipDisabledError(),
    ],
)
def test_tenant_context_dependency_preserves_resolution_failures(
    exception: Exception,
) -> None:
    principal = build_principal()
    recording_session = RecordingSession()
    fake_service = FakeResolveTenantContextService(exception)

    with pytest.raises(type(exception)) as raised:
        get_tenant_context(
            tenant_id=uuid4(),
            principal=principal,
            session=cast(Session, recording_session),
            service=cast(
                ResolveTenantContextService,
                fake_service,
            ),
        )

    assert raised.value is exception
    assert recording_session.commit_count == 0


def test_permission_dependency_preserves_denial() -> None:
    principal = build_principal()
    tenant_context = build_tenant_context(
        principal,
        role=TenantRole.STAFF,
    )
    exception = TenantPermissionDeniedError()
    fake_service = FakeRequireTenantPermissionService(exception)
    dependency = require_tenant_permission(TenantPermission.MEMBER_READ)

    with pytest.raises(TenantPermissionDeniedError) as raised:
        dependency(
            tenant_context=tenant_context,
            service=cast(
                RequireTenantPermissionService,
                fake_service,
            ),
        )

    assert raised.value is exception


def build_tenant_test_app(
    *,
    resolution_result: TenantContext | Exception,
    permission_result: AuthorizedTenantContext | Exception,
) -> tuple[
    FastAPI,
    FakeResolveTenantContextService,
    FakeRequireTenantPermissionService,
    RecordingSession,
    dict[str, int],
]:
    """Build an isolated tenant-protected API."""

    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)

    principal = build_principal()
    fake_resolution_service = FakeResolveTenantContextService(resolution_result)
    fake_permission_service = FakeRequireTenantPermissionService(permission_result)
    recording_session = RecordingSession()
    handler_state = {"call_count": 0}

    def override_principal() -> AuthenticatedPrincipal:
        return principal

    def override_database_session() -> Session:
        return cast(Session, recording_session)

    def override_resolution_service() -> ResolveTenantContextService:
        return cast(
            ResolveTenantContextService,
            fake_resolution_service,
        )

    def override_permission_service() -> RequireTenantPermissionService:
        return cast(
            RequireTenantPermissionService,
            fake_permission_service,
        )

    application.dependency_overrides[get_authenticated_principal] = override_principal
    application.dependency_overrides[get_database_session] = override_database_session
    application.dependency_overrides[get_resolve_tenant_context_service] = (
        override_resolution_service
    )
    application.dependency_overrides[get_require_tenant_permission_service] = (
        override_permission_service
    )

    @application.get("/tenants/{tenant_id}")
    def read_tenant(
        tenant_context: TenantContextDependency,
        authorized_context: TenantReadAuthorizationDependency,
    ) -> dict[str, str]:
        handler_state["call_count"] += 1

        return {
            "tenant_id": str(tenant_context.tenant_id),
            "permission": authorized_context.granted_permission,
        }

    return (
        application,
        fake_resolution_service,
        fake_permission_service,
        recording_session,
        handler_state,
    )


def test_request_resolves_tenant_once_and_enforces_permission_once() -> None:
    principal = build_principal()
    tenant_id = uuid4()
    tenant_context = build_tenant_context(
        principal,
        tenant_id=tenant_id,
    )
    authorized_context = build_authorized_context(
        tenant_context,
        TenantPermission.TENANT_READ,
    )
    (
        application,
        resolution_service,
        permission_service,
        recording_session,
        handler_state,
    ) = build_tenant_test_app(
        resolution_result=tenant_context,
        permission_result=authorized_context,
    )

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.get(f"/tenants/{tenant_id}")

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": str(tenant_id),
        "permission": TenantPermission.TENANT_READ,
    }
    assert resolution_service.call_count == 1
    assert permission_service.call_count == 1
    assert handler_state["call_count"] == 1
    assert recording_session.commit_count == 0


@pytest.mark.parametrize(
    ("exception", "expected_status", "expected_code"),
    [
        (
            TenantNotFoundError(),
            404,
            "tenant_not_found",
        ),
        (
            TenantMembershipNotFoundError(),
            404,
            "tenant_membership_not_found",
        ),
        (
            TenantDisabledError(),
            403,
            "tenant_disabled",
        ),
        (
            TenantMembershipDisabledError(),
            403,
            "tenant_membership_disabled",
        ),
    ],
)
def test_resolution_failure_short_circuits_permission_and_handler(
    exception: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    principal = build_principal()
    tenant_context = build_tenant_context(principal)
    authorized_context = build_authorized_context(
        tenant_context,
        TenantPermission.TENANT_READ,
    )
    (
        application,
        resolution_service,
        permission_service,
        recording_session,
        handler_state,
    ) = build_tenant_test_app(
        resolution_result=exception,
        permission_result=authorized_context,
    )

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.get(f"/tenants/{tenant_context.tenant_id}")

    body = response.json()

    assert response.status_code == expected_status
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["type"] == (f"urn:clinicops:problem:{expected_code}")
    assert body["status"] == expected_status
    assert body["code"] == expected_code
    assert resolution_service.call_count == 1
    assert permission_service.call_count == 0
    assert handler_state["call_count"] == 0
    assert recording_session.commit_count == 0


def test_permission_denial_short_circuits_handler() -> None:
    principal = build_principal()
    tenant_context = build_tenant_context(
        principal,
        role=TenantRole.STAFF,
    )
    (
        application,
        resolution_service,
        permission_service,
        recording_session,
        handler_state,
    ) = build_tenant_test_app(
        resolution_result=tenant_context,
        permission_result=TenantPermissionDeniedError(),
    )

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.get(f"/tenants/{tenant_context.tenant_id}")

    body = response.json()

    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["type"] == ("urn:clinicops:problem:tenant_permission_denied")
    assert body["status"] == 403
    assert body["code"] == "tenant_permission_denied"
    assert resolution_service.call_count == 1
    assert permission_service.call_count == 1
    assert handler_state["call_count"] == 0
    assert recording_session.commit_count == 0


def test_openapi_declares_bearer_security_and_uuid_tenant_path() -> None:
    principal = build_principal()
    tenant_context = build_tenant_context(principal)
    authorized_context = build_authorized_context(
        tenant_context,
        TenantPermission.TENANT_READ,
    )
    application, _, _, _, _ = build_tenant_test_app(
        resolution_result=tenant_context,
        permission_result=authorized_context,
    )
    openapi_schema = application.openapi()
    operation = openapi_schema["paths"]["/tenants/{tenant_id}"]["get"]

    tenant_parameter = next(
        parameter for parameter in operation["parameters"] if parameter["name"] == "tenant_id"
    )

    assert tenant_parameter["in"] == "path"
    assert tenant_parameter["required"] is True
    assert tenant_parameter["schema"]["type"] == "string"
    assert tenant_parameter["schema"]["format"] == "uuid"
    assert {"HTTPBearer": []} in operation["security"]
    assert openapi_schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
