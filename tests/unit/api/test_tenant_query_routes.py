from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from clinicops.api.dependencies import get_database_session
from clinicops.api.v1.authentication.dependencies import (
    get_authenticated_principal,
)
from clinicops.api.v1.tenants.dependencies import (
    get_list_available_tenants_service,
    get_list_tenant_memberships_service,
    get_tenant_context,
    get_tenant_details_service,
)
from clinicops.api.v1.tenants.routes import router
from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
)
from clinicops.authorization.services.resolve_tenant_context import (
    TenantContext,
)
from clinicops.tenancy.models import (
    MembershipStatus,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.services.queries import (
    AvailableTenant,
    GetTenantDetailsCommand,
    GetTenantDetailsService,
    ListAvailableTenantsCommand,
    ListAvailableTenantsService,
    ListTenantMembershipsCommand,
    ListTenantMembershipsService,
    TenantDetails,
    TenantMembership,
)

FIXED_NOW = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)


class RecordingSession:
    """Track accidental commits in read-only tenant routes."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeListAvailableTenantsService:
    def __init__(
        self,
        results: tuple[AvailableTenant, ...],
    ) -> None:
        self.results = results
        self.received_session: Session | None = None
        self.received_command: ListAvailableTenantsCommand | None = None

    def execute(
        self,
        session: Session,
        command: ListAvailableTenantsCommand,
    ) -> tuple[AvailableTenant, ...]:
        self.received_session = session
        self.received_command = command
        return self.results


class FakeGetTenantDetailsService:
    def __init__(self, result: TenantDetails) -> None:
        self.result = result
        self.received_session: Session | None = None
        self.received_command: GetTenantDetailsCommand | None = None

    def execute(
        self,
        session: Session,
        command: GetTenantDetailsCommand,
    ) -> TenantDetails:
        self.received_session = session
        self.received_command = command
        return self.result


class FakeListTenantMembershipsService:
    def __init__(
        self,
        results: tuple[TenantMembership, ...],
    ) -> None:
        self.results = results
        self.received_session: Session | None = None
        self.received_command: ListTenantMembershipsCommand | None = None

    def execute(
        self,
        session: Session,
        command: ListTenantMembershipsCommand,
    ) -> tuple[TenantMembership, ...]:
        self.received_session = session
        self.received_command = command
        return self.results


def build_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=uuid4(),
        session_id=uuid4(),
        access_token_id=uuid4(),
        authenticated_at=FIXED_NOW - timedelta(minutes=5),
        access_token_expires_at=FIXED_NOW + timedelta(minutes=10),
        session_expires_at=FIXED_NOW + timedelta(days=29),
    )


def build_test_app() -> tuple[
    FastAPI,
    AuthenticatedPrincipal,
    TenantContext,
    RecordingSession,
    FakeListAvailableTenantsService,
    FakeGetTenantDetailsService,
    FakeListTenantMembershipsService,
]:
    principal = build_principal()
    tenant_id = uuid4()
    membership_id = uuid4()
    context = TenantContext(
        user_id=principal.user_id,
        session_id=principal.session_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        role=TenantRole.OWNER,
    )
    session = RecordingSession()
    available_result = AvailableTenant(
        tenant_id=tenant_id,
        tenant_name="North Clinic",
        tenant_status=TenantStatus.ACTIVE,
        membership_id=membership_id,
        membership_role=TenantRole.OWNER,
        membership_status=MembershipStatus.ACTIVE,
    )
    detail_result = TenantDetails(
        id=tenant_id,
        name="North Clinic",
        status=TenantStatus.ACTIVE,
        created_at=FIXED_NOW - timedelta(days=90),
        updated_at=FIXED_NOW,
        disabled_at=None,
    )
    membership_result = TenantMembership(
        id=membership_id,
        tenant_id=tenant_id,
        user_id=principal.user_id,
        role=TenantRole.OWNER,
        status=MembershipStatus.ACTIVE,
        created_at=FIXED_NOW - timedelta(days=90),
        updated_at=FIXED_NOW,
        disabled_at=None,
    )
    available_service = FakeListAvailableTenantsService((available_result,))
    detail_service = FakeGetTenantDetailsService(detail_result)
    membership_service = FakeListTenantMembershipsService((membership_result,))

    application = FastAPI()
    application.include_router(router)

    def override_principal() -> AuthenticatedPrincipal:
        return principal

    def override_session() -> Session:
        return cast(Session, session)

    def override_tenant_context() -> TenantContext:
        return context

    def override_available_service() -> ListAvailableTenantsService:
        return cast(
            ListAvailableTenantsService,
            available_service,
        )

    def override_detail_service() -> GetTenantDetailsService:
        return cast(
            GetTenantDetailsService,
            detail_service,
        )

    def override_membership_service() -> ListTenantMembershipsService:
        return cast(
            ListTenantMembershipsService,
            membership_service,
        )

    application.dependency_overrides[get_authenticated_principal] = override_principal
    application.dependency_overrides[get_database_session] = override_session
    application.dependency_overrides[get_tenant_context] = override_tenant_context
    application.dependency_overrides[get_list_available_tenants_service] = (
        override_available_service
    )
    application.dependency_overrides[get_tenant_details_service] = override_detail_service
    application.dependency_overrides[get_list_tenant_memberships_service] = (
        override_membership_service
    )

    return (
        application,
        principal,
        context,
        session,
        available_service,
        detail_service,
        membership_service,
    )


def test_list_available_tenants_uses_global_principal() -> None:
    (
        application,
        principal,
        context,
        session,
        available_service,
        _,
        _,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.get("/tenants")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(context.tenant_id),
                "name": "North Clinic",
                "status": "active",
                "current_membership": {
                    "id": str(context.membership_id),
                    "role": "owner",
                    "status": "active",
                },
            }
        ]
    }
    assert available_service.received_command == (
        ListAvailableTenantsCommand(
            user_id=principal.user_id,
        )
    )
    assert isinstance(available_service.received_session, RecordingSession)
    assert available_service.received_session is session
    assert session.commit_count == 0


def test_get_tenant_details_uses_authorized_tenant_context() -> None:
    (
        application,
        _,
        context,
        session,
        _,
        detail_service,
        _,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.get(f"/tenants/{context.tenant_id}")

    body = response.json()

    assert response.status_code == 200
    assert body["id"] == str(context.tenant_id)
    assert body["current_membership"] == {
        "id": str(context.membership_id),
        "role": "owner",
        "status": "active",
    }
    assert detail_service.received_command == (
        GetTenantDetailsCommand(
            tenant_id=context.tenant_id,
        )
    )
    assert isinstance(detail_service.received_session, RecordingSession)
    assert detail_service.received_session is session
    assert session.commit_count == 0


def test_list_tenant_memberships_uses_authorized_tenant_context() -> None:
    (
        application,
        principal,
        context,
        session,
        _,
        _,
        membership_service,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.get(f"/tenants/{context.tenant_id}/memberships")

    body = response.json()

    assert response.status_code == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == str(context.membership_id)
    assert body["items"][0]["tenant_id"] == str(context.tenant_id)
    assert body["items"][0]["user_id"] == str(principal.user_id)
    assert body["items"][0]["role"] == "owner"
    assert body["items"][0]["status"] == "active"
    assert body["items"][0]["disabled_at"] is None
    assert membership_service.received_command == (
        ListTenantMembershipsCommand(
            tenant_id=context.tenant_id,
        )
    )
    assert isinstance(membership_service.received_session, RecordingSession)
    assert membership_service.received_session is session
    assert session.commit_count == 0


def test_tenant_query_openapi_contract() -> None:
    application, _, _, _, _, _, _ = build_test_app()
    schema = application.openapi()
    paths = schema["paths"]

    assert "/tenants" in paths
    assert "/tenants/{tenant_id}" in paths
    assert "/tenants/{tenant_id}/memberships" in paths

    for path in (
        "/tenants",
        "/tenants/{tenant_id}",
        "/tenants/{tenant_id}/memberships",
    ):
        assert {"HTTPBearer": []} in paths[path]["get"]["security"]

    detail_parameter = next(
        parameter
        for parameter in paths["/tenants/{tenant_id}"]["get"]["parameters"]
        if parameter["name"] == "tenant_id"
    )
    assert detail_parameter["in"] == "path"
    assert detail_parameter["required"] is True
    assert detail_parameter["schema"]["type"] == "string"
    assert detail_parameter["schema"]["format"] == "uuid"
