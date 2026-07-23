from datetime import UTC, datetime
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
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from clinicops.api.v1.invitations.dependencies import (
    get_accept_invitation_service,
)
from clinicops.api.v1.invitations.routes import router
from clinicops.audit.enums import AuditActorType, AuditSource
from clinicops.invitations.exceptions import (
    InvitationTokenInvalidError,
)
from clinicops.invitations.services.accept_invitation import (
    AcceptedInvitation,
    AcceptInvitationCommand,
    AcceptInvitationService,
)
from clinicops.tenancy.models import TenantRole

FIXED_NOW = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)


class RecordingSession:
    """Track route-owned invitation acceptance commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeAcceptInvitationService:
    """Return one deterministic acceptance result or failure."""

    def __init__(
        self,
        result: AcceptedInvitation | Exception,
    ) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: AcceptInvitationCommand | None = None

    def execute(
        self,
        session: Session,
        command: AcceptInvitationCommand,
    ) -> AcceptedInvitation:
        self.call_count += 1
        self.received_session = session
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


def build_test_app(
    *,
    service_result: AcceptedInvitation | Exception | None = None,
) -> tuple[
    FastAPI,
    RecordingSession,
    FakeAcceptInvitationService,
    AcceptedInvitation,
]:
    """Build an isolated invitation acceptance API."""

    accepted_invitation = AcceptedInvitation(
        invitation_id=uuid4(),
        tenant_id=uuid4(),
        membership_id=uuid4(),
        user_id=uuid4(),
        role=TenantRole.STAFF,
        user_was_created=True,
        accepted_at=FIXED_NOW,
    )
    session = RecordingSession()
    fake_service = FakeAcceptInvitationService(
        accepted_invitation if service_result is None else service_result
    )
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)
    application.include_router(router)

    def override_session() -> Session:
        return cast(Session, session)

    def override_service() -> AcceptInvitationService:
        return cast(
            AcceptInvitationService,
            fake_service,
        )

    application.dependency_overrides[get_database_session] = override_session
    application.dependency_overrides[get_accept_invitation_service] = override_service

    return (
        application,
        session,
        fake_service,
        accepted_invitation,
    )


def test_accept_invitation_dependency_builds_service() -> None:
    assert isinstance(
        get_accept_invitation_service(),
        AcceptInvitationService,
    )


def test_accept_invitation_translates_secrets_and_commits() -> None:
    (
        application,
        session,
        service,
        accepted_invitation,
    ) = build_test_app()
    token = " token-with-deliberate-whitespace "
    password = " Password-With-Spaces-2026 "
    request_id = str(uuid4())
    correlation_id = str(uuid4())

    with TestClient(application) as client:
        response = client.post(
            "/invitations/accept",
            json={
                "token": token,
                "password": password,
            },
            headers={
                REQUEST_ID_HEADER: request_id,
                CORRELATION_ID_HEADER: correlation_id,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "invitation_id": str(accepted_invitation.invitation_id),
        "tenant_id": str(accepted_invitation.tenant_id),
        "membership_id": str(accepted_invitation.membership_id),
        "user_id": str(accepted_invitation.user_id),
        "role": "staff",
        "user_was_created": True,
        "accepted_at": FIXED_NOW.isoformat().replace(
            "+00:00",
            "Z",
        ),
    }
    assert service.call_count == 1
    assert service.received_session is cast(Session, session)
    assert service.received_command is not None
    assert service.received_command.token == token
    assert service.received_command.password == password
    assert service.received_command.audit_context.actor.actor_type is AuditActorType.SYSTEM
    assert service.received_command.audit_context.actor.user_id is None
    assert service.received_command.audit_context.actor.role is None
    assert service.received_command.audit_context.source is AuditSource.HTTP
    assert service.received_command.audit_context.request_id == request_id
    assert service.received_command.audit_context.correlation_id == correlation_id
    assert session.commit_count == 1
    assert "token" not in response.json()
    assert "password" not in response.json()


def test_accept_invitation_allows_missing_password() -> None:
    (
        application,
        session,
        service,
        _,
    ) = build_test_app()
    token = "existing-user-token"
    request_id = str(uuid4())
    correlation_id = str(uuid4())

    with TestClient(application) as client:
        response = client.post(
            "/invitations/accept",
            json={
                "token": token,
            },
            headers={
                REQUEST_ID_HEADER: request_id,
                CORRELATION_ID_HEADER: correlation_id,
            },
        )

    assert response.status_code == 200
    assert service.received_command is not None
    assert service.received_command.token == token
    assert service.received_command.password is None
    assert service.received_command.audit_context.actor.actor_type is AuditActorType.SYSTEM
    assert service.received_command.audit_context.source is AuditSource.HTTP
    assert service.received_command.audit_context.request_id == request_id
    assert service.received_command.audit_context.correlation_id == correlation_id
    assert session.commit_count == 1


def test_accept_invitation_rejects_identity_override_before_service() -> None:
    (
        application,
        session,
        service,
        _,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.post(
            "/invitations/accept",
            json={
                "token": "opaque-token",
                "tenant_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["code"] == "request_validation_error"
    assert service.call_count == 0
    assert session.commit_count == 0


def test_accept_invitation_failure_does_not_commit() -> None:
    (
        application,
        session,
        service,
        _,
    ) = build_test_app(
        service_result=InvitationTokenInvalidError(),
    )

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/invitations/accept",
            json={
                "token": "unknown-token",
            },
        )

    assert response.status_code == 400
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["code"] == "invitation_token_invalid"
    assert service.call_count == 1
    assert session.commit_count == 0


def test_accept_invitation_openapi_is_public_and_declares_response() -> None:
    application, _, _, _ = build_test_app()
    operation = application.openapi()["paths"]["/invitations/accept"]["post"]

    assert "security" not in operation
    assert "200" in operation["responses"]
    assert operation["summary"] == ("Accept a tenant invitation")
