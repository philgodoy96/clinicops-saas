from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from clinicops.api.dependencies import get_database_session
from clinicops.api.errors import register_exception_handlers
from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from clinicops.api.v1.professionals import (
    get_archive_professional_service,
    get_create_professional_service,
    get_get_professional_service,
    get_link_professional_membership_service,
    get_list_professionals_service,
    get_restore_professional_service,
    get_unlink_professional_membership_service,
    get_update_professional_service,
    router,
)
from clinicops.api.v1.tenants.dependencies import get_tenant_context
from clinicops.audit.enums import AuditSource
from clinicops.authorization.services.resolve_tenant_context import (
    TenantContext,
)
from clinicops.professionals.contracts import (
    ArchivedProfessional,
    ArchiveProfessionalCommand,
    CreatedProfessional,
    CreateProfessionalCommand,
    GetProfessionalCommand,
    LinkedProfessionalMembership,
    LinkProfessionalMembershipCommand,
    ProfessionalRecord,
    RestoredProfessional,
    RestoreProfessionalCommand,
    UnlinkedProfessionalMembership,
    UnlinkProfessionalMembershipCommand,
    UpdatedProfessional,
    UpdateProfessionalCommand,
)
from clinicops.professionals.enums import (
    ProfessionalListStatus,
    ProfessionalMutableField,
    ProfessionalStatus,
)
from clinicops.professionals.exceptions import (
    ProfessionalInvalidRegistrationError,
)
from clinicops.professionals.services.archive_professional import (
    ArchiveProfessionalService,
)
from clinicops.professionals.services.create_professional import (
    CreateProfessionalService,
)
from clinicops.professionals.services.get_professional import (
    GetProfessionalService,
)
from clinicops.professionals.services.link_professional_membership import (
    LinkProfessionalMembershipService,
)
from clinicops.professionals.services.list_professionals import (
    ListedProfessionals,
    ListProfessionalsQuery,
    ListProfessionalsService,
)
from clinicops.professionals.services.restore_professional import (
    RestoreProfessionalService,
)
from clinicops.professionals.services.unlink_professional_membership import (
    UnlinkProfessionalMembershipService,
)
from clinicops.professionals.services.update_professional import (
    UpdateProfessionalService,
)
from clinicops.tenancy.models import TenantRole

_FIXED_NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


class RecordingSession:
    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class RecordingService:
    def __init__(self, result: object) -> None:
        self.result = result
        self.commands: list[object] = []

    def execute(self, command: object) -> object:
        self.commands.append(command)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass(slots=True)
class RouteHarness:
    application: FastAPI
    context: TenantContext
    session: RecordingSession
    professional: ProfessionalRecord
    create_service: RecordingService
    list_service: RecordingService
    get_service: RecordingService
    update_service: RecordingService
    archive_service: RecordingService
    restore_service: RecordingService
    link_service: RecordingService
    unlink_service: RecordingService


def _professional(
    *,
    tenant_id: UUID,
    professional_id: UUID | None = None,
    membership_id: UUID | None = None,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    version: int = 1,
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=professional_id or uuid4(),
        tenant_id=tenant_id,
        membership_id=membership_id,
        full_name="Jordan Lee",
        specialty="Cardiology",
        registration_number="CRM-12345",
        registration_region="SP",
        email="jordan@example.com",
        phone=None,
        external_reference=None,
        status=status,
        version=version,
        created_at=_FIXED_NOW,
        updated_at=_FIXED_NOW,
    )


def _assert_http_audit_context(
    command: (
        CreateProfessionalCommand
        | UpdateProfessionalCommand
        | ArchiveProfessionalCommand
        | RestoreProfessionalCommand
        | LinkProfessionalMembershipCommand
        | UnlinkProfessionalMembershipCommand
    ),
    *,
    context: TenantContext,
    request_id: str,
    correlation_id: str,
) -> None:
    audit_context = command.audit_context
    assert audit_context.actor.user_id == context.user_id
    assert audit_context.actor.role == context.role.value
    assert audit_context.source is AuditSource.HTTP
    assert audit_context.request_id == request_id
    assert audit_context.correlation_id == correlation_id


def _context_headers() -> tuple[dict[str, str], str, str]:
    request_id = str(uuid4())
    correlation_id = str(uuid4())
    return (
        {
            REQUEST_ID_HEADER: request_id,
            CORRELATION_ID_HEADER: correlation_id,
        },
        request_id,
        correlation_id,
    )


def build_test_app(
    *,
    create_result: object | None = None,
) -> RouteHarness:
    tenant_id = uuid4()
    professional = _professional(tenant_id=tenant_id)
    context = TenantContext(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid4(),
        role=TenantRole.OWNER,
    )
    session = RecordingSession()
    create_service = RecordingService(
        CreatedProfessional(professional=professional) if create_result is None else create_result
    )
    list_service = RecordingService(
        ListedProfessionals(
            items=(professional,),
            next_cursor="next-page",
        )
    )
    get_service = RecordingService(professional)
    update_service = RecordingService(
        UpdatedProfessional(
            professional=_professional(
                tenant_id=tenant_id,
                professional_id=professional.id,
                version=2,
            ),
            changed_fields=(ProfessionalMutableField.EMAIL,),
        )
    )
    archive_service = RecordingService(
        ArchivedProfessional(
            professional=_professional(
                tenant_id=tenant_id,
                professional_id=professional.id,
                status=ProfessionalStatus.ARCHIVED,
                version=2,
            )
        )
    )
    restore_service = RecordingService(
        RestoredProfessional(
            professional=_professional(
                tenant_id=tenant_id,
                professional_id=professional.id,
                status=ProfessionalStatus.ACTIVE,
                version=3,
            )
        )
    )
    linked_membership_id = uuid4()
    link_service = RecordingService(
        LinkedProfessionalMembership(
            professional=_professional(
                tenant_id=tenant_id,
                professional_id=professional.id,
                membership_id=linked_membership_id,
                version=2,
            )
        )
    )
    unlink_service = RecordingService(
        UnlinkedProfessionalMembership(
            professional=_professional(
                tenant_id=tenant_id,
                professional_id=professional.id,
                membership_id=None,
                version=3,
            ),
            previous_membership_id=linked_membership_id,
        )
    )

    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)
    application.include_router(router)

    def override_tenant_context() -> TenantContext:
        return context

    def override_session() -> Session:
        return cast(Session, session)

    def override_create_service() -> CreateProfessionalService:
        return cast(CreateProfessionalService, create_service)

    def override_list_service() -> ListProfessionalsService:
        return cast(ListProfessionalsService, list_service)

    def override_get_service() -> GetProfessionalService:
        return cast(GetProfessionalService, get_service)

    def override_update_service() -> UpdateProfessionalService:
        return cast(UpdateProfessionalService, update_service)

    def override_archive_service() -> ArchiveProfessionalService:
        return cast(ArchiveProfessionalService, archive_service)

    def override_restore_service() -> RestoreProfessionalService:
        return cast(RestoreProfessionalService, restore_service)

    def override_link_service() -> LinkProfessionalMembershipService:
        return cast(LinkProfessionalMembershipService, link_service)

    def override_unlink_service() -> UnlinkProfessionalMembershipService:
        return cast(UnlinkProfessionalMembershipService, unlink_service)

    application.dependency_overrides[get_tenant_context] = override_tenant_context
    application.dependency_overrides[get_database_session] = override_session
    application.dependency_overrides[get_create_professional_service] = override_create_service
    application.dependency_overrides[get_list_professionals_service] = override_list_service
    application.dependency_overrides[get_get_professional_service] = override_get_service
    application.dependency_overrides[get_update_professional_service] = override_update_service
    application.dependency_overrides[get_archive_professional_service] = override_archive_service
    application.dependency_overrides[get_restore_professional_service] = override_restore_service
    application.dependency_overrides[get_link_professional_membership_service] = (
        override_link_service
    )
    application.dependency_overrides[get_unlink_professional_membership_service] = (
        override_unlink_service
    )

    return RouteHarness(
        application=application,
        context=context,
        session=session,
        professional=professional,
        create_service=create_service,
        list_service=list_service,
        get_service=get_service,
        update_service=update_service,
        archive_service=archive_service,
        restore_service=restore_service,
        link_service=link_service,
        unlink_service=unlink_service,
    )


def test_create_route_returns_201_and_commits_once() -> None:
    harness = build_test_app()
    untrusted_tenant_id = uuid4()
    headers, request_id, correlation_id = _context_headers()

    with TestClient(harness.application) as client:
        response = client.post(
            f"/tenants/{untrusted_tenant_id}/professionals",
            json={"full_name": "Jordan Lee"},
            headers=headers,
        )

    command = harness.create_service.commands[0]
    assert response.status_code == 201
    assert isinstance(command, CreateProfessionalCommand)
    assert command.tenant_id == harness.context.tenant_id
    assert command.tenant_id != untrusted_tenant_id
    assert command.full_name == "Jordan Lee"
    _assert_http_audit_context(
        command,
        context=harness.context,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    assert response.json()["id"] == str(harness.professional.id)
    assert harness.session.commit_count == 1


def test_list_route_returns_200_without_commit() -> None:
    harness = build_test_app()
    untrusted_tenant_id = uuid4()

    with TestClient(harness.application) as client:
        response = client.get(
            f"/tenants/{untrusted_tenant_id}/professionals",
            params={
                "limit": 25,
                "status": "all",
                "search": "Jordan",
                "cursor": "opaque",
            },
        )

    query = harness.list_service.commands[0]
    assert response.status_code == 200
    assert isinstance(query, ListProfessionalsQuery)
    assert query.tenant_id == harness.context.tenant_id
    assert query.tenant_id != untrusted_tenant_id
    assert query.limit == 25
    assert query.status is ProfessionalListStatus.ALL
    assert query.search == "Jordan"
    assert query.cursor == "opaque"
    assert response.json()["next_cursor"] == "next-page"
    assert [item["id"] for item in response.json()["items"]] == [str(harness.professional.id)]
    assert harness.session.commit_count == 0


def test_get_route_returns_200_without_commit() -> None:
    harness = build_test_app()
    untrusted_tenant_id = uuid4()

    with TestClient(harness.application) as client:
        response = client.get(
            (f"/tenants/{untrusted_tenant_id}/professionals/{harness.professional.id}"),
        )

    command = harness.get_service.commands[0]
    assert response.status_code == 200
    assert isinstance(command, GetProfessionalCommand)
    assert command.tenant_id == harness.context.tenant_id
    assert command.tenant_id != untrusted_tenant_id
    assert command.professional_id == harness.professional.id
    assert response.json()["id"] == str(harness.professional.id)
    assert harness.session.commit_count == 0


def test_update_route_maps_model_fields_set_and_commits_once() -> None:
    harness = build_test_app()
    untrusted_tenant_id = uuid4()
    headers, request_id, correlation_id = _context_headers()

    with TestClient(harness.application) as client:
        response = client.patch(
            (f"/tenants/{untrusted_tenant_id}/professionals/{harness.professional.id}"),
            json={
                "expected_version": 1,
                "email": None,
            },
            headers=headers,
        )

    command = harness.update_service.commands[0]
    assert response.status_code == 200
    assert isinstance(command, UpdateProfessionalCommand)
    assert command.tenant_id == harness.context.tenant_id
    assert command.tenant_id != untrusted_tenant_id
    assert command.professional_id == harness.professional.id
    assert command.expected_version == 1
    assert command.fields_to_update == frozenset({ProfessionalMutableField.EMAIL})
    assert command.email is None
    _assert_http_audit_context(
        command,
        context=harness.context,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    assert response.json()["version"] == 2
    assert harness.session.commit_count == 1


def test_archive_and_restore_routes_commit_once_each() -> None:
    harness = build_test_app()
    untrusted_tenant_id = uuid4()
    professional_id = harness.professional.id
    archive_headers, archive_request_id, archive_correlation_id = _context_headers()
    restore_headers, restore_request_id, restore_correlation_id = _context_headers()

    with TestClient(harness.application) as client:
        archive_response = client.post(
            (f"/tenants/{untrusted_tenant_id}/professionals/{professional_id}/archive"),
            json={"expected_version": 1},
            headers=archive_headers,
        )
        restore_response = client.post(
            (f"/tenants/{untrusted_tenant_id}/professionals/{professional_id}/restore"),
            json={"expected_version": 2},
            headers=restore_headers,
        )

    archive_command = harness.archive_service.commands[0]
    restore_command = harness.restore_service.commands[0]
    assert archive_response.status_code == 200
    assert restore_response.status_code == 200
    assert isinstance(archive_command, ArchiveProfessionalCommand)
    assert isinstance(restore_command, RestoreProfessionalCommand)
    assert archive_command.tenant_id == harness.context.tenant_id
    assert restore_command.tenant_id == harness.context.tenant_id
    assert archive_command.tenant_id != untrusted_tenant_id
    assert restore_command.tenant_id != untrusted_tenant_id
    assert archive_command.expected_version == 1
    assert restore_command.expected_version == 2
    _assert_http_audit_context(
        archive_command,
        context=harness.context,
        request_id=archive_request_id,
        correlation_id=archive_correlation_id,
    )
    _assert_http_audit_context(
        restore_command,
        context=harness.context,
        request_id=restore_request_id,
        correlation_id=restore_correlation_id,
    )
    assert archive_response.json()["status"] == ProfessionalStatus.ARCHIVED
    assert restore_response.json()["status"] == ProfessionalStatus.ACTIVE
    assert harness.session.commit_count == 2


def test_link_membership_route_passes_membership_and_commits_once() -> None:
    harness = build_test_app()
    untrusted_tenant_id = uuid4()
    membership_id = uuid4()
    headers, request_id, correlation_id = _context_headers()

    with TestClient(harness.application) as client:
        response = client.post(
            (
                f"/tenants/{untrusted_tenant_id}/professionals/"
                f"{harness.professional.id}/link-membership"
            ),
            json={
                "membership_id": str(membership_id),
                "expected_version": 1,
            },
            headers=headers,
        )

    command = harness.link_service.commands[0]
    assert response.status_code == 200
    assert isinstance(command, LinkProfessionalMembershipCommand)
    assert command.tenant_id == harness.context.tenant_id
    assert command.tenant_id != untrusted_tenant_id
    assert command.membership_id == membership_id
    assert command.expected_version == 1
    _assert_http_audit_context(
        command,
        context=harness.context,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    assert response.json()["membership_id"] is not None
    assert harness.session.commit_count == 1


def test_unlink_membership_route_passes_version_and_commits_once() -> None:
    harness = build_test_app()
    untrusted_tenant_id = uuid4()
    headers, request_id, correlation_id = _context_headers()

    with TestClient(harness.application) as client:
        response = client.post(
            (
                f"/tenants/{untrusted_tenant_id}/professionals/"
                f"{harness.professional.id}/unlink-membership"
            ),
            json={"expected_version": 2},
            headers=headers,
        )

    command = harness.unlink_service.commands[0]
    assert response.status_code == 200
    assert isinstance(command, UnlinkProfessionalMembershipCommand)
    assert command.tenant_id == harness.context.tenant_id
    assert command.tenant_id != untrusted_tenant_id
    assert command.expected_version == 2
    assert {field.name for field in fields(command)} == {
        "tenant_id",
        "professional_id",
        "expected_version",
        "audit_context",
    }
    _assert_http_audit_context(
        command,
        context=harness.context,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    assert response.json()["membership_id"] is None
    assert harness.session.commit_count == 1


def test_mutation_route_does_not_commit_when_service_fails() -> None:
    failure = ProfessionalInvalidRegistrationError()
    harness = build_test_app(create_result=failure)
    headers, _, _ = _context_headers()

    with TestClient(
        harness.application,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            f"/tenants/{harness.context.tenant_id}/professionals",
            json={"full_name": "Jordan Lee"},
            headers=headers,
        )

    assert response.status_code == 400
    assert response.json()["code"] == failure.code
    assert len(harness.create_service.commands) == 1
    assert harness.session.commit_count == 0


def test_openapi_exposes_bearer_security_and_approved_operations() -> None:
    harness = build_test_app()
    paths = harness.application.openapi()["paths"]

    collection_path = "/tenants/{tenant_id}/professionals"
    item_path = "/tenants/{tenant_id}/professionals/{professional_id}"
    archive_path = "/tenants/{tenant_id}/professionals/{professional_id}/archive"
    restore_path = "/tenants/{tenant_id}/professionals/{professional_id}/restore"
    link_path = "/tenants/{tenant_id}/professionals/{professional_id}/link-membership"
    unlink_path = "/tenants/{tenant_id}/professionals/{professional_id}/unlink-membership"

    assert set(paths[collection_path]) == {"get", "post"}
    assert set(paths[item_path]) == {"get", "patch"}
    assert set(paths[archive_path]) == {"post"}
    assert set(paths[restore_path]) == {"post"}
    assert set(paths[link_path]) == {"post"}
    assert set(paths[unlink_path]) == {"post"}
    assert "delete" not in paths[item_path]

    operations_with_body = [
        paths[collection_path]["post"],
        paths[item_path]["patch"],
        paths[archive_path]["post"],
        paths[restore_path]["post"],
        paths[link_path]["post"],
        paths[unlink_path]["post"],
    ]
    operations_without_body = [
        paths[collection_path]["get"],
        paths[item_path]["get"],
    ]

    for operation in operations_with_body:
        assert {"HTTPBearer": []} in operation["security"]
        assert "requestBody" in operation

    for operation in operations_without_body:
        assert {"HTTPBearer": []} in operation["security"]
        assert "requestBody" not in operation

    assert paths[collection_path]["post"]["responses"]["201"] is not None
