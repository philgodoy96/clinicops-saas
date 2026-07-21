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
from clinicops.api.v1.tenants.dependencies import (
    get_issue_invitation_service,
    get_list_tenant_invitations_service,
    get_revoke_invitation_service,
    get_tenant_context,
)
from clinicops.api.v1.tenants.routes import router
from clinicops.authorization.services.resolve_tenant_context import (
    TenantContext,
)
from clinicops.invitations.exceptions import (
    InvitationAlreadyPendingError,
)
from clinicops.invitations.models import InvitationStatus
from clinicops.invitations.services.issue_invitation import (
    IssuedInvitation,
    IssueInvitationCommand,
    IssueInvitationService,
)
from clinicops.invitations.services.queries import (
    ListTenantInvitationsCommand,
    ListTenantInvitationsService,
    TenantInvitation,
)
from clinicops.invitations.services.revoke_invitation import (
    RevokedInvitation,
    RevokeInvitationCommand,
    RevokeInvitationService,
)
from clinicops.tenancy.models import TenantRole

FIXED_NOW = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)


class RecordingSession:
    """Track route-owned invitation commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeListTenantInvitationsService:
    def __init__(
        self,
        results: tuple[TenantInvitation, ...],
    ) -> None:
        self.results = results
        self.received_session: Session | None = None
        self.received_command: ListTenantInvitationsCommand | None = None

    def execute(
        self,
        session: Session,
        command: ListTenantInvitationsCommand,
    ) -> tuple[TenantInvitation, ...]:
        self.received_session = session
        self.received_command = command
        return self.results


class FakeIssueInvitationService:
    def __init__(
        self,
        result: IssuedInvitation | Exception,
    ) -> None:
        self.result = result
        self.received_session: Session | None = None
        self.received_command: IssueInvitationCommand | None = None

    def execute(
        self,
        session: Session,
        command: IssueInvitationCommand,
    ) -> IssuedInvitation:
        self.received_session = session
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


class FakeRevokeInvitationService:
    def __init__(
        self,
        result: RevokedInvitation | Exception,
    ) -> None:
        self.result = result
        self.received_session: Session | None = None
        self.received_command: RevokeInvitationCommand | None = None

    def execute(
        self,
        session: Session,
        command: RevokeInvitationCommand,
    ) -> RevokedInvitation:
        self.received_session = session
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


def build_test_app(
    *,
    issue_result: IssuedInvitation | Exception | None = None,
) -> tuple[
    FastAPI,
    TenantContext,
    RecordingSession,
    FakeListTenantInvitationsService,
    FakeIssueInvitationService,
    FakeRevokeInvitationService,
]:
    tenant_id = uuid4()
    user_id = uuid4()
    context = TenantContext(
        user_id=user_id,
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid4(),
        role=TenantRole.OWNER,
    )
    invitation_id = uuid4()
    list_result = TenantInvitation(
        id=invitation_id,
        tenant_id=tenant_id,
        invited_email="member@example.com",
        role=TenantRole.STAFF,
        status=InvitationStatus.PENDING,
        expires_at=FIXED_NOW + timedelta(days=7),
        accepted_at=None,
        revoked_at=None,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )
    default_issue_result = IssuedInvitation(
        id=invitation_id,
        tenant_id=tenant_id,
        invited_email="member@example.com",
        role=TenantRole.STAFF,
        expires_at=FIXED_NOW + timedelta(days=7),
        token="one-time-secret",
    )
    revoke_result = RevokedInvitation(
        invitation_id=invitation_id,
        tenant_id=tenant_id,
        revoked_at=FIXED_NOW + timedelta(minutes=5),
    )
    list_service = FakeListTenantInvitationsService((list_result,))
    issue_service = FakeIssueInvitationService(
        default_issue_result if issue_result is None else issue_result
    )
    revoke_service = FakeRevokeInvitationService(revoke_result)
    session = RecordingSession()
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)
    application.include_router(router)

    def override_context() -> TenantContext:
        return context

    def override_session() -> Session:
        return cast(Session, session)

    def override_list_service() -> ListTenantInvitationsService:
        return cast(
            ListTenantInvitationsService,
            list_service,
        )

    def override_issue_service() -> IssueInvitationService:
        return cast(
            IssueInvitationService,
            issue_service,
        )

    def override_revoke_service() -> RevokeInvitationService:
        return cast(
            RevokeInvitationService,
            revoke_service,
        )

    application.dependency_overrides[get_tenant_context] = override_context
    application.dependency_overrides[get_database_session] = override_session
    application.dependency_overrides[get_list_tenant_invitations_service] = override_list_service
    application.dependency_overrides[get_issue_invitation_service] = override_issue_service
    application.dependency_overrides[get_revoke_invitation_service] = override_revoke_service

    return (
        application,
        context,
        session,
        list_service,
        issue_service,
        revoke_service,
    )


def test_list_tenant_invitations_uses_authorized_context_without_commit() -> None:
    (
        application,
        context,
        session,
        list_service,
        _,
        _,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.get(f"/tenants/{context.tenant_id}/invitations")

    body = response.json()

    assert response.status_code == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["tenant_id"] == str(context.tenant_id)
    assert body["items"][0]["status"] == "pending"
    assert "token" not in body["items"][0]
    assert "token_digest" not in body["items"][0]
    assert list_service.received_command == (
        ListTenantInvitationsCommand(
            tenant_id=context.tenant_id,
        )
    )
    assert list_service.received_session is cast(Session, session)
    assert session.commit_count == 0


def test_issue_tenant_invitation_uses_context_and_commits() -> None:
    (
        application,
        context,
        session,
        _,
        issue_service,
        _,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.post(
            f"/tenants/{context.tenant_id}/invitations",
            json={
                "invited_email": "member@example.com",
                "role": "staff",
            },
        )

    body = response.json()

    assert response.status_code == 201
    assert body["tenant_id"] == str(context.tenant_id)
    assert body["token"] == "one-time-secret"
    assert issue_service.received_command == (
        IssueInvitationCommand(
            tenant_id=context.tenant_id,
            issuer_user_id=context.user_id,
            invited_email="member@example.com",
            role=TenantRole.STAFF,
        )
    )
    assert issue_service.received_session is cast(Session, session)
    assert session.commit_count == 1


def test_revoke_tenant_invitation_uses_context_and_commits() -> None:
    (
        application,
        context,
        session,
        list_service,
        _,
        revoke_service,
    ) = build_test_app()
    invitation_id = list_service.results[0].id

    with TestClient(application) as client:
        response = client.post(f"/tenants/{context.tenant_id}/invitations/{invitation_id}/revoke")

    assert response.status_code == 200
    assert response.json()["invitation_id"] == str(invitation_id)
    assert revoke_service.received_command == (
        RevokeInvitationCommand(
            tenant_id=context.tenant_id,
            invitation_id=invitation_id,
            actor_user_id=context.user_id,
        )
    )
    assert revoke_service.received_session is cast(Session, session)
    assert session.commit_count == 1


def test_issue_failure_does_not_commit_or_expose_token() -> None:
    (
        application,
        context,
        session,
        _,
        _,
        _,
    ) = build_test_app(issue_result=InvitationAlreadyPendingError())

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            f"/tenants/{context.tenant_id}/invitations",
            json={
                "invited_email": "member@example.com",
                "role": "staff",
            },
        )

    body = response.json()

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["code"] == "invitation_already_pending"
    assert "token" not in body
    assert session.commit_count == 0


def test_invitation_routes_declare_security_and_response_contracts() -> None:
    application, _, _, _, _, _ = build_test_app()
    paths = application.openapi()["paths"]

    collection = "/tenants/{tenant_id}/invitations"
    revocation = "/tenants/{tenant_id}/invitations/{invitation_id}/revoke"

    assert collection in paths
    assert revocation in paths
    assert {"HTTPBearer": []} in paths[collection]["get"]["security"]
    assert {"HTTPBearer": []} in paths[collection]["post"]["security"]
    assert {"HTTPBearer": []} in paths[revocation]["post"]["security"]
    assert paths[collection]["post"]["responses"]["201"]
    assert paths[revocation]["post"]["responses"]["200"]
