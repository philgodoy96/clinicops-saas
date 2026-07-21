from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

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
from clinicops.api.v1.tenants.dependencies import (
    get_change_membership_role_service,
    get_disable_membership_service,
    get_enable_membership_service,
    get_remove_membership_service,
    get_tenant_context,
)
from clinicops.api.v1.tenants.routes import router
from clinicops.authorization.services.resolve_tenant_context import (
    TenantContext,
)
from clinicops.tenancy.exceptions import (
    MembershipOwnerProtectedError,
)
from clinicops.tenancy.models import TenantRole
from clinicops.tenancy.services.change_membership_role import (
    ChangeMembershipRoleService,
)
from clinicops.tenancy.services.disable_membership import (
    DisableMembershipService,
)
from clinicops.tenancy.services.enable_membership import (
    EnableMembershipService,
)
from clinicops.tenancy.services.membership_administration import (
    ChangedMembershipRole,
    ChangeMembershipRoleCommand,
    DisabledMembership,
    DisableMembershipCommand,
    EnabledMembership,
    EnableMembershipCommand,
    RemovedMembership,
    RemoveMembershipCommand,
)
from clinicops.tenancy.services.remove_membership import (
    RemoveMembershipService,
)

FIXED_NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


class RecordingSession:
    """Track route-owned membership administration commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeChangeMembershipRoleService:
    """Return one deterministic role result or failure."""

    def __init__(
        self,
        result: ChangedMembershipRole | Exception,
    ) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: ChangeMembershipRoleCommand | None = None

    def execute(
        self,
        session: Session,
        command: ChangeMembershipRoleCommand,
    ) -> ChangedMembershipRole:
        self.call_count += 1
        self.received_session = session
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


class FakeDisableMembershipService:
    """Return one deterministic disable result."""

    def __init__(self, result: DisabledMembership) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: DisableMembershipCommand | None = None

    def execute(
        self,
        session: Session,
        command: DisableMembershipCommand,
    ) -> DisabledMembership:
        self.call_count += 1
        self.received_session = session
        self.received_command = command

        return self.result


class FakeEnableMembershipService:
    """Return one deterministic enable result."""

    def __init__(self, result: EnabledMembership) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: EnableMembershipCommand | None = None

    def execute(
        self,
        session: Session,
        command: EnableMembershipCommand,
    ) -> EnabledMembership:
        self.call_count += 1
        self.received_session = session
        self.received_command = command

        return self.result


class FakeRemoveMembershipService:
    """Return one deterministic removal result."""

    def __init__(self, result: RemovedMembership) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: RemoveMembershipCommand | None = None

    def execute(
        self,
        session: Session,
        command: RemoveMembershipCommand,
    ) -> RemovedMembership:
        self.call_count += 1
        self.received_session = session
        self.received_command = command

        return self.result


@dataclass(slots=True)
class RouteHarness:
    """Isolated membership administration route state."""

    application: FastAPI
    context: TenantContext
    target_membership_id: UUID
    session: RecordingSession
    role_service: FakeChangeMembershipRoleService
    disable_service: FakeDisableMembershipService
    enable_service: FakeEnableMembershipService
    remove_service: FakeRemoveMembershipService
    changed_role: ChangedMembershipRole
    disabled: DisabledMembership
    enabled: EnabledMembership
    removed: RemovedMembership


def build_test_app(
    *,
    context_role: TenantRole = TenantRole.OWNER,
    role_result: ChangedMembershipRole | Exception | None = None,
) -> RouteHarness:
    """Build an isolated membership administration API."""

    tenant_id = uuid4()
    actor_user_id = uuid4()
    actor_membership_id = uuid4()
    target_membership_id = uuid4()
    target_user_id = uuid4()
    context = TenantContext(
        user_id=actor_user_id,
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=actor_membership_id,
        role=context_role,
    )
    changed_role = ChangedMembershipRole(
        membership_id=target_membership_id,
        tenant_id=tenant_id,
        user_id=target_user_id,
        previous_role=TenantRole.STAFF,
        role=TenantRole.ADMIN,
        updated_at=FIXED_NOW,
    )
    disabled = DisabledMembership(
        membership_id=target_membership_id,
        tenant_id=tenant_id,
        user_id=target_user_id,
        role=TenantRole.STAFF,
        disabled_at=FIXED_NOW,
    )
    enabled = EnabledMembership(
        membership_id=target_membership_id,
        tenant_id=tenant_id,
        user_id=target_user_id,
        role=TenantRole.ADMIN,
        updated_at=FIXED_NOW,
    )
    removed = RemovedMembership(
        membership_id=target_membership_id,
        tenant_id=tenant_id,
        user_id=target_user_id,
    )
    session = RecordingSession()
    role_service = FakeChangeMembershipRoleService(
        changed_role if role_result is None else role_result
    )
    disable_service = FakeDisableMembershipService(disabled)
    enable_service = FakeEnableMembershipService(enabled)
    remove_service = FakeRemoveMembershipService(removed)
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)
    application.include_router(router)

    def override_tenant_context() -> TenantContext:
        return context

    def override_session() -> Session:
        return cast(Session, session)

    def override_role_service() -> ChangeMembershipRoleService:
        return cast(
            ChangeMembershipRoleService,
            role_service,
        )

    def override_disable_service() -> DisableMembershipService:
        return cast(
            DisableMembershipService,
            disable_service,
        )

    def override_enable_service() -> EnableMembershipService:
        return cast(
            EnableMembershipService,
            enable_service,
        )

    def override_remove_service() -> RemoveMembershipService:
        return cast(
            RemoveMembershipService,
            remove_service,
        )

    application.dependency_overrides[get_tenant_context] = override_tenant_context
    application.dependency_overrides[get_database_session] = override_session
    application.dependency_overrides[get_change_membership_role_service] = override_role_service
    application.dependency_overrides[get_disable_membership_service] = override_disable_service
    application.dependency_overrides[get_enable_membership_service] = override_enable_service
    application.dependency_overrides[get_remove_membership_service] = override_remove_service

    return RouteHarness(
        application=application,
        context=context,
        target_membership_id=target_membership_id,
        session=session,
        role_service=role_service,
        disable_service=disable_service,
        enable_service=enable_service,
        remove_service=remove_service,
        changed_role=changed_role,
        disabled=disabled,
        enabled=enabled,
        removed=removed,
    )


def test_membership_administration_dependencies_build_services() -> None:
    assert isinstance(
        get_change_membership_role_service(),
        ChangeMembershipRoleService,
    )
    assert isinstance(
        get_disable_membership_service(),
        DisableMembershipService,
    )
    assert isinstance(
        get_enable_membership_service(),
        EnableMembershipService,
    )
    assert isinstance(
        get_remove_membership_service(),
        RemoveMembershipService,
    )


def test_change_membership_role_uses_authorized_context_and_commits() -> None:
    harness = build_test_app()

    with TestClient(harness.application) as client:
        response = client.patch(
            (
                f"/tenants/{harness.context.tenant_id}/memberships/"
                f"{harness.target_membership_id}/role"
            ),
            json={"role": "admin"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "membership_id": str(harness.changed_role.membership_id),
        "tenant_id": str(harness.context.tenant_id),
        "user_id": str(harness.changed_role.user_id),
        "previous_role": "staff",
        "role": "admin",
        "updated_at": FIXED_NOW.isoformat().replace(
            "+00:00",
            "Z",
        ),
    }
    assert harness.role_service.call_count == 1
    assert harness.role_service.received_session is cast(Session, harness.session)
    assert harness.role_service.received_command == (
        ChangeMembershipRoleCommand(
            tenant_id=harness.context.tenant_id,
            actor_user_id=harness.context.user_id,
            membership_id=harness.target_membership_id,
            role=TenantRole.ADMIN,
        )
    )
    assert harness.session.commit_count == 1


def test_disable_membership_uses_path_without_request_body_and_commits() -> None:
    harness = build_test_app()

    with TestClient(harness.application) as client:
        response = client.post(
            f"/tenants/{harness.context.tenant_id}/memberships/"
            f"{harness.target_membership_id}/disable"
        )

    assert response.status_code == 200
    assert response.json()["disabled_at"] == (FIXED_NOW.isoformat().replace("+00:00", "Z"))
    assert harness.disable_service.received_command == (
        DisableMembershipCommand(
            tenant_id=harness.context.tenant_id,
            actor_user_id=harness.context.user_id,
            membership_id=harness.target_membership_id,
        )
    )
    assert harness.session.commit_count == 1


def test_enable_membership_uses_path_without_request_body_and_commits() -> None:
    harness = build_test_app()

    with TestClient(harness.application) as client:
        response = client.post(
            f"/tenants/{harness.context.tenant_id}/memberships/"
            f"{harness.target_membership_id}/enable"
        )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert harness.enable_service.received_command == (
        EnableMembershipCommand(
            tenant_id=harness.context.tenant_id,
            actor_user_id=harness.context.user_id,
            membership_id=harness.target_membership_id,
        )
    )
    assert harness.session.commit_count == 1


def test_remove_membership_returns_empty_204_and_commits() -> None:
    harness = build_test_app()

    with TestClient(harness.application) as client:
        response = client.delete(
            f"/tenants/{harness.context.tenant_id}/memberships/{harness.target_membership_id}"
        )

    assert response.status_code == 204
    assert response.content == b""
    assert harness.remove_service.received_command == (
        RemoveMembershipCommand(
            tenant_id=harness.context.tenant_id,
            actor_user_id=harness.context.user_id,
            membership_id=harness.target_membership_id,
        )
    )
    assert harness.session.commit_count == 1


def test_role_request_rejects_owner_before_service_execution() -> None:
    harness = build_test_app()

    with TestClient(harness.application) as client:
        response = client.patch(
            (
                f"/tenants/{harness.context.tenant_id}/memberships/"
                f"{harness.target_membership_id}/role"
            ),
            json={"role": "owner"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["code"] == "request_validation_error"
    assert harness.role_service.call_count == 0
    assert harness.session.commit_count == 0


def test_member_manage_permission_is_required_before_service_execution() -> None:
    harness = build_test_app(context_role=TenantRole.STAFF)

    with TestClient(
        harness.application,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            f"/tenants/{harness.context.tenant_id}/memberships/"
            f"{harness.target_membership_id}/disable"
        )

    assert response.status_code == 403
    assert harness.disable_service.call_count == 0
    assert harness.session.commit_count == 0


def test_application_failure_does_not_commit() -> None:
    harness = build_test_app(
        role_result=MembershipOwnerProtectedError(),
    )

    with TestClient(
        harness.application,
        raise_server_exceptions=False,
    ) as client:
        response = client.patch(
            (
                f"/tenants/{harness.context.tenant_id}/memberships/"
                f"{harness.target_membership_id}/role"
            ),
            json={"role": "admin"},
        )

    assert 400 <= response.status_code < 500
    assert response.json()["code"] == "membership_owner_protected"
    assert harness.role_service.call_count == 1
    assert harness.session.commit_count == 0


def test_membership_administration_openapi_declares_security_and_responses() -> None:
    harness = build_test_app()
    paths = harness.application.openapi()["paths"]
    operations = [
        (
            paths["/tenants/{tenant_id}/memberships/{membership_id}/role"]["patch"],
            "200",
        ),
        (
            paths["/tenants/{tenant_id}/memberships/{membership_id}/disable"]["post"],
            "200",
        ),
        (
            paths["/tenants/{tenant_id}/memberships/{membership_id}/enable"]["post"],
            "200",
        ),
        (
            paths["/tenants/{tenant_id}/memberships/{membership_id}"]["delete"],
            "204",
        ),
    ]

    for operation, success_status in operations:
        assert {"HTTPBearer": []} in operation["security"]
        assert success_status in operation["responses"]

    role_operation = operations[0][0]
    disable_operation = operations[1][0]
    enable_operation = operations[2][0]
    remove_operation = operations[3][0]

    assert "requestBody" in role_operation
    assert "requestBody" not in disable_operation
    assert "requestBody" not in enable_operation
    assert "requestBody" not in remove_operation
