from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from fastapi import FastAPI
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
    get_create_tenant_service,
)
from clinicops.api.v1.tenants.routes import router
from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
)
from clinicops.tenancy.exceptions import (
    InvalidTenantNameError,
    TenantNameViolation,
)
from clinicops.tenancy.models import TenantStatus
from clinicops.tenancy.services.create_tenant import (
    CreatedTenant,
    CreateTenantCommand,
    CreateTenantService,
)

FIXED_NOW = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)


class RecordingSession:
    """Track route-owned tenant creation commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeCreateTenantService:
    """Return one deterministic tenant result or failure."""

    def __init__(
        self,
        result: CreatedTenant | Exception,
    ) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: CreateTenantCommand | None = None

    def execute(
        self,
        session: Session,
        command: CreateTenantCommand,
    ) -> CreatedTenant:
        self.call_count += 1
        self.received_session = session
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


def build_test_app(
    *,
    service_result: CreatedTenant | Exception | None = None,
) -> tuple[
    FastAPI,
    AuthenticatedPrincipal,
    RecordingSession,
    FakeCreateTenantService,
    CreatedTenant,
]:
    """Build an isolated tenant creation API."""

    principal = build_principal()
    created_tenant = CreatedTenant(
        id=uuid4(),
        name="Northstar Health Clinic",
        status=TenantStatus.ACTIVE,
        owner_user_id=principal.user_id,
        created_at=FIXED_NOW,
    )
    session = RecordingSession()
    fake_service = FakeCreateTenantService(
        created_tenant if service_result is None else service_result
    )
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)
    application.include_router(router)

    def override_principal() -> AuthenticatedPrincipal:
        return principal

    def override_session() -> Session:
        return cast(Session, session)

    def override_service() -> CreateTenantService:
        return cast(CreateTenantService, fake_service)

    application.dependency_overrides[get_authenticated_principal] = override_principal
    application.dependency_overrides[get_database_session] = override_session
    application.dependency_overrides[get_create_tenant_service] = override_service

    return (
        application,
        principal,
        session,
        fake_service,
        created_tenant,
    )


def test_create_tenant_dependency_builds_service() -> None:
    assert isinstance(
        get_create_tenant_service(),
        CreateTenantService,
    )


def test_create_tenant_uses_authenticated_user_and_commits() -> None:
    (
        application,
        principal,
        session,
        service,
        created_tenant,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.post(
            "/tenants",
            json={
                "name": "  Northstar Health Clinic  ",
            },
        )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(created_tenant.id),
        "name": created_tenant.name,
        "status": "active",
        "owner_user_id": str(principal.user_id),
        "created_at": FIXED_NOW.isoformat().replace(
            "+00:00",
            "Z",
        ),
    }
    assert service.call_count == 1
    assert service.received_session is cast(Session, session)
    assert service.received_command == CreateTenantCommand(
        name="Northstar Health Clinic",
        owner_user_id=principal.user_id,
    )
    assert session.commit_count == 1


def test_create_tenant_rejects_caller_controlled_owner() -> None:
    (
        application,
        _,
        session,
        service,
        _,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.post(
            "/tenants",
            json={
                "name": "Northstar Health Clinic",
                "owner_user_id": str(uuid4()),
            },
        )

    body = response.json()

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["code"] == "request_validation_error"
    assert service.call_count == 0
    assert session.commit_count == 0


def test_create_tenant_failure_does_not_commit() -> None:
    (
        application,
        _,
        session,
        service,
        _,
    ) = build_test_app(
        service_result=InvalidTenantNameError(TenantNameViolation.TOO_SHORT),
    )

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/tenants",
            json={
                "name": "Northstar Health Clinic",
            },
        )

    assert response.status_code == 400
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["code"] == "invalid_tenant_name"
    assert service.call_count == 1
    assert session.commit_count == 0


def test_create_tenant_openapi_declares_bearer_and_created_response() -> None:
    application, _, _, _, _ = build_test_app()
    operation = application.openapi()["paths"]["/tenants"]["post"]

    assert {"HTTPBearer": []} in operation["security"]
    assert "201" in operation["responses"]
    assert operation["summary"] == "Create a tenant"
