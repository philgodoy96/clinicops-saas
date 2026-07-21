from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
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
    get_tenant_context,
    get_transfer_tenant_ownership_service,
)
from clinicops.api.v1.tenants.routes import router
from clinicops.api.v1.tenants.schemas import (
    TransferredTenantOwnershipResponse,
    TransferTenantOwnershipRequest,
)
from clinicops.authorization.services.resolve_tenant_context import (
    TenantContext,
)
from clinicops.tenancy.exceptions import (
    TenantOwnershipConflictError,
)
from clinicops.tenancy.models import TenantRole
from clinicops.tenancy.services.transfer_ownership import (
    TransferredTenantOwnership,
    TransferTenantOwnershipCommand,
    TransferTenantOwnershipService,
)


class RecordingSession:
    """Track route-owned ownership-transfer commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeTransferTenantOwnershipService:
    """Return one deterministic transfer result or failure."""

    def __init__(
        self,
        result: TransferredTenantOwnership | Exception,
    ) -> None:
        self.result = result
        self.call_count = 0
        self.received_session: Session | None = None
        self.received_command: TransferTenantOwnershipCommand | None = None

    def execute(
        self,
        session: Session,
        command: TransferTenantOwnershipCommand,
    ) -> TransferredTenantOwnership:
        self.call_count += 1
        self.received_session = session
        self.received_command = command

        if isinstance(self.result, Exception):
            raise self.result

        return self.result


def build_test_app(
    *,
    context_role: TenantRole = TenantRole.OWNER,
    service_result: (TransferredTenantOwnership | Exception | None) = None,
) -> tuple[
    FastAPI,
    TenantContext,
    UUID,
    RecordingSession,
    FakeTransferTenantOwnershipService,
    TransferredTenantOwnership,
]:
    """Build an isolated ownership-transfer API."""

    tenant_id = uuid4()
    current_owner_user_id = uuid4()
    new_owner_user_id = uuid4()
    context = TenantContext(
        user_id=current_owner_user_id,
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid4(),
        role=context_role,
    )
    transferred = TransferredTenantOwnership(
        tenant_id=tenant_id,
        previous_owner_user_id=current_owner_user_id,
        new_owner_user_id=new_owner_user_id,
    )
    session = RecordingSession()
    fake_service = FakeTransferTenantOwnershipService(
        transferred if service_result is None else service_result
    )
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)
    application.include_router(router)

    def override_tenant_context() -> TenantContext:
        return context

    def override_session() -> Session:
        return cast(Session, session)

    def override_service() -> TransferTenantOwnershipService:
        return cast(
            TransferTenantOwnershipService,
            fake_service,
        )

    application.dependency_overrides[get_tenant_context] = override_tenant_context
    application.dependency_overrides[get_database_session] = override_session
    application.dependency_overrides[get_transfer_tenant_ownership_service] = override_service

    return (
        application,
        context,
        new_owner_user_id,
        session,
        fake_service,
        transferred,
    )


def test_transfer_ownership_dependency_builds_service() -> None:
    assert isinstance(
        get_transfer_tenant_ownership_service(),
        TransferTenantOwnershipService,
    )


def test_transfer_ownership_request_accepts_only_new_owner_user() -> None:
    new_owner_user_id = uuid4()

    request = TransferTenantOwnershipRequest(
        new_owner_user_id=new_owner_user_id,
    )

    assert request.new_owner_user_id == new_owner_user_id


@pytest.mark.parametrize(
    "field_name",
    [
        "tenant_id",
        "expected_current_owner_user_id",
        "previous_owner_user_id",
        "new_owner_membership_id",
    ],
)
def test_transfer_ownership_request_rejects_trusted_fields(
    field_name: str,
) -> None:
    payload: dict[str, object] = {
        "new_owner_user_id": str(uuid4()),
        field_name: str(uuid4()),
    }

    with pytest.raises(ValidationError):
        TransferTenantOwnershipRequest.model_validate(payload)


def test_transfer_ownership_response_matches_application_result() -> None:
    result = TransferredTenantOwnership(
        tenant_id=uuid4(),
        previous_owner_user_id=uuid4(),
        new_owner_user_id=uuid4(),
    )

    response = TransferredTenantOwnershipResponse.model_validate(result)

    assert response.model_dump() == {
        "tenant_id": result.tenant_id,
        "previous_owner_user_id": (result.previous_owner_user_id),
        "new_owner_user_id": result.new_owner_user_id,
    }


def test_owner_context_translates_command_and_commits() -> None:
    (
        application,
        context,
        new_owner_user_id,
        session,
        service,
        transferred,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.post(
            (f"/tenants/{context.tenant_id}/ownership/transfer"),
            json={
                "new_owner_user_id": str(new_owner_user_id),
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": str(transferred.tenant_id),
        "previous_owner_user_id": str(transferred.previous_owner_user_id),
        "new_owner_user_id": str(transferred.new_owner_user_id),
    }
    assert service.call_count == 1
    assert service.received_session is cast(Session, session)
    assert service.received_command == (
        TransferTenantOwnershipCommand(
            tenant_id=context.tenant_id,
            expected_current_owner_user_id=context.user_id,
            new_owner_user_id=new_owner_user_id,
        )
    )
    assert session.commit_count == 1


@pytest.mark.parametrize(
    "context_role",
    [
        TenantRole.ADMIN,
        TenantRole.STAFF,
    ],
)
def test_non_owner_is_rejected_before_service_execution(
    context_role: TenantRole,
) -> None:
    (
        application,
        context,
        new_owner_user_id,
        session,
        service,
        _,
    ) = build_test_app(context_role=context_role)

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            (f"/tenants/{context.tenant_id}/ownership/transfer"),
            json={
                "new_owner_user_id": str(new_owner_user_id),
            },
        )

    assert response.status_code == 403
    assert service.call_count == 0
    assert session.commit_count == 0


def test_trusted_owner_override_is_rejected_before_service_execution() -> None:
    (
        application,
        context,
        new_owner_user_id,
        session,
        service,
        _,
    ) = build_test_app()

    with TestClient(application) as client:
        response = client.post(
            (f"/tenants/{context.tenant_id}/ownership/transfer"),
            json={
                "new_owner_user_id": str(new_owner_user_id),
                "expected_current_owner_user_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["code"] == "request_validation_error"
    assert service.call_count == 0
    assert session.commit_count == 0


def test_application_failure_does_not_commit() -> None:
    (
        application,
        context,
        new_owner_user_id,
        session,
        service,
        _,
    ) = build_test_app(
        service_result=TenantOwnershipConflictError(),
    )

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            (f"/tenants/{context.tenant_id}/ownership/transfer"),
            json={
                "new_owner_user_id": str(new_owner_user_id),
            },
        )

    assert 400 <= response.status_code < 500
    assert response.json()["code"] == ("tenant_ownership_conflict")
    assert service.call_count == 1
    assert session.commit_count == 0


def test_ownership_transfer_openapi_declares_security_and_response() -> None:
    application, _, _, _, _, _ = build_test_app()
    operation = application.openapi()["paths"]["/tenants/{tenant_id}/ownership/transfer"]["post"]

    assert {"HTTPBearer": []} in operation["security"]
    assert "requestBody" in operation
    assert "200" in operation["responses"]
    assert operation["summary"] == "Transfer tenant ownership"
