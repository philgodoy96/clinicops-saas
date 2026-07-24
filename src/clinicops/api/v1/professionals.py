from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from clinicops.api.dependencies import DatabaseSessionDependency
from clinicops.api.v1.tenants.dependencies import (
    require_tenant_permission,
)
from clinicops.audit.context import AuditRecordingContext
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.core.request_context import (
    get_correlation_id,
    get_request_id,
)
from clinicops.professionals.contracts import (
    ArchiveProfessionalCommand,
    CreateProfessionalCommand,
    GetProfessionalCommand,
    LinkProfessionalMembershipCommand,
    ProfessionalRecord,
    RestoreProfessionalCommand,
    UnlinkProfessionalMembershipCommand,
    UpdateProfessionalCommand,
)
from clinicops.professionals.enums import (
    ProfessionalListStatus,
    ProfessionalMutableField,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.schemas import (
    ProfessionalCreateRequest,
    ProfessionalListResponse,
    ProfessionalMembershipLinkRequest,
    ProfessionalResponse,
    ProfessionalUpdateRequest,
    ProfessionalVersionRequest,
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
from clinicops.professionals.validation import SEARCH_MAX_LENGTH

_MAX_CURSOR_LENGTH = 1024

router = APIRouter(
    prefix="/tenants",
    tags=["professionals"],
)

ProfessionalReadAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PROFESSIONAL_READ)),
]
ProfessionalCreateAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PROFESSIONAL_CREATE)),
]
ProfessionalUpdateAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PROFESSIONAL_UPDATE)),
]
ProfessionalArchiveAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PROFESSIONAL_ARCHIVE)),
]
ProfessionalRestoreAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PROFESSIONAL_RESTORE)),
]
ProfessionalLinkAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PROFESSIONAL_LINK_MEMBERSHIP)),
]
ProfessionalUnlinkAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PROFESSIONAL_UNLINK_MEMBERSHIP)),
]


def get_create_professional_service(
    session: DatabaseSessionDependency,
) -> CreateProfessionalService:
    return CreateProfessionalService(
        ProfessionalRepository(session),
        session,
    )


def get_get_professional_service(
    session: DatabaseSessionDependency,
) -> GetProfessionalService:
    return GetProfessionalService(ProfessionalRepository(session))


def get_list_professionals_service(
    session: DatabaseSessionDependency,
) -> ListProfessionalsService:
    return ListProfessionalsService(ProfessionalRepository(session))


def get_update_professional_service(
    session: DatabaseSessionDependency,
) -> UpdateProfessionalService:
    return UpdateProfessionalService(
        ProfessionalRepository(session),
        session,
    )


def get_archive_professional_service(
    session: DatabaseSessionDependency,
) -> ArchiveProfessionalService:
    return ArchiveProfessionalService(
        ProfessionalRepository(session),
        session,
    )


def get_restore_professional_service(
    session: DatabaseSessionDependency,
) -> RestoreProfessionalService:
    return RestoreProfessionalService(
        ProfessionalRepository(session),
        session,
    )


def get_link_professional_membership_service(
    session: DatabaseSessionDependency,
) -> LinkProfessionalMembershipService:
    return LinkProfessionalMembershipService(
        ProfessionalRepository(session),
        session,
    )


def get_unlink_professional_membership_service(
    session: DatabaseSessionDependency,
) -> UnlinkProfessionalMembershipService:
    return UnlinkProfessionalMembershipService(
        ProfessionalRepository(session),
        session,
    )


CreateProfessionalServiceDependency = Annotated[
    CreateProfessionalService,
    Depends(get_create_professional_service),
]
GetProfessionalServiceDependency = Annotated[
    GetProfessionalService,
    Depends(get_get_professional_service),
]
ListProfessionalsServiceDependency = Annotated[
    ListProfessionalsService,
    Depends(get_list_professionals_service),
]
UpdateProfessionalServiceDependency = Annotated[
    UpdateProfessionalService,
    Depends(get_update_professional_service),
]
ArchiveProfessionalServiceDependency = Annotated[
    ArchiveProfessionalService,
    Depends(get_archive_professional_service),
]
RestoreProfessionalServiceDependency = Annotated[
    RestoreProfessionalService,
    Depends(get_restore_professional_service),
]
LinkProfessionalMembershipServiceDependency = Annotated[
    LinkProfessionalMembershipService,
    Depends(get_link_professional_membership_service),
]
UnlinkProfessionalMembershipServiceDependency = Annotated[
    UnlinkProfessionalMembershipService,
    Depends(get_unlink_professional_membership_service),
]


@router.post(
    "/{tenant_id}/professionals",
    response_model=ProfessionalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Professional",
)
def create_professional(
    payload: ProfessionalCreateRequest,
    context: ProfessionalCreateAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: CreateProfessionalServiceDependency,
) -> ProfessionalResponse:
    result = service.execute(
        CreateProfessionalCommand(
            tenant_id=context.tenant_id,
            full_name=payload.full_name,
            specialty=payload.specialty,
            registration_number=payload.registration_number,
            registration_region=payload.registration_region,
            email=payload.email,
            phone=payload.phone,
            external_reference=payload.external_reference,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _professional_response(result.professional)


@router.get(
    "/{tenant_id}/professionals",
    response_model=ProfessionalListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Professionals",
)
def list_professionals(
    context: ProfessionalReadAuthorizationDependency,
    service: ListProfessionalsServiceDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status_filter: Annotated[
        ProfessionalListStatus,
        Query(alias="status"),
    ] = ProfessionalListStatus.ACTIVE,
    search: Annotated[
        str | None,
        Query(max_length=SEARCH_MAX_LENGTH),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(max_length=_MAX_CURSOR_LENGTH),
    ] = None,
) -> ProfessionalListResponse:
    result = service.execute(
        ListProfessionalsQuery(
            tenant_id=context.tenant_id,
            limit=limit,
            status=status_filter,
            search=search,
            cursor=cursor,
        )
    )
    return ProfessionalListResponse(
        items=[_professional_response(professional) for professional in result.items],
        next_cursor=result.next_cursor,
    )


@router.get(
    "/{tenant_id}/professionals/{professional_id}",
    response_model=ProfessionalResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a Professional",
)
def get_professional(
    professional_id: UUID,
    context: ProfessionalReadAuthorizationDependency,
    service: GetProfessionalServiceDependency,
) -> ProfessionalResponse:
    professional = service.execute(
        GetProfessionalCommand(
            tenant_id=context.tenant_id,
            professional_id=professional_id,
        )
    )
    return _professional_response(professional)


@router.patch(
    "/{tenant_id}/professionals/{professional_id}",
    response_model=ProfessionalResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a Professional",
)
def update_professional(
    payload: ProfessionalUpdateRequest,
    professional_id: UUID,
    context: ProfessionalUpdateAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: UpdateProfessionalServiceDependency,
) -> ProfessionalResponse:
    fields_to_update = frozenset(
        ProfessionalMutableField(field_name)
        for field_name in payload.model_fields_set
        if field_name != "expected_version"
    )
    result = service.execute(
        UpdateProfessionalCommand(
            tenant_id=context.tenant_id,
            professional_id=professional_id,
            expected_version=payload.expected_version,
            fields_to_update=fields_to_update,
            full_name=payload.full_name,
            specialty=payload.specialty,
            registration_number=payload.registration_number,
            registration_region=payload.registration_region,
            email=payload.email,
            phone=payload.phone,
            external_reference=payload.external_reference,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _professional_response(result.professional)


@router.post(
    "/{tenant_id}/professionals/{professional_id}/archive",
    response_model=ProfessionalResponse,
    status_code=status.HTTP_200_OK,
    summary="Archive a Professional",
)
def archive_professional(
    payload: ProfessionalVersionRequest,
    professional_id: UUID,
    context: ProfessionalArchiveAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: ArchiveProfessionalServiceDependency,
) -> ProfessionalResponse:
    result = service.execute(
        ArchiveProfessionalCommand(
            tenant_id=context.tenant_id,
            professional_id=professional_id,
            expected_version=payload.expected_version,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _professional_response(result.professional)


@router.post(
    "/{tenant_id}/professionals/{professional_id}/restore",
    response_model=ProfessionalResponse,
    status_code=status.HTTP_200_OK,
    summary="Restore a Professional",
)
def restore_professional(
    payload: ProfessionalVersionRequest,
    professional_id: UUID,
    context: ProfessionalRestoreAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: RestoreProfessionalServiceDependency,
) -> ProfessionalResponse:
    result = service.execute(
        RestoreProfessionalCommand(
            tenant_id=context.tenant_id,
            professional_id=professional_id,
            expected_version=payload.expected_version,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _professional_response(result.professional)


@router.post(
    "/{tenant_id}/professionals/{professional_id}/link-membership",
    response_model=ProfessionalResponse,
    status_code=status.HTTP_200_OK,
    summary="Link a Professional Membership",
)
def link_professional_membership(
    payload: ProfessionalMembershipLinkRequest,
    professional_id: UUID,
    context: ProfessionalLinkAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: LinkProfessionalMembershipServiceDependency,
) -> ProfessionalResponse:
    result = service.execute(
        LinkProfessionalMembershipCommand(
            tenant_id=context.tenant_id,
            professional_id=professional_id,
            membership_id=payload.membership_id,
            expected_version=payload.expected_version,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _professional_response(result.professional)


@router.post(
    "/{tenant_id}/professionals/{professional_id}/unlink-membership",
    response_model=ProfessionalResponse,
    status_code=status.HTTP_200_OK,
    summary="Unlink a Professional Membership",
)
def unlink_professional_membership(
    payload: ProfessionalVersionRequest,
    professional_id: UUID,
    context: ProfessionalUnlinkAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: UnlinkProfessionalMembershipServiceDependency,
) -> ProfessionalResponse:
    result = service.execute(
        UnlinkProfessionalMembershipCommand(
            tenant_id=context.tenant_id,
            professional_id=professional_id,
            expected_version=payload.expected_version,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _professional_response(result.professional)


def _professional_response(
    professional: ProfessionalRecord,
) -> ProfessionalResponse:
    return ProfessionalResponse.model_validate(professional)


def _http_audit_context(
    context: AuthorizedTenantContext,
) -> AuditRecordingContext:
    request_id = get_request_id()
    correlation_id = get_correlation_id()
    if request_id is None or correlation_id is None:
        raise RuntimeError("Request context identifiers are required for audit recording.")

    return AuditRecordingContext.http_user(
        user_id=context.user_id,
        role=context.role.value,
        request_id=request_id,
        correlation_id=correlation_id,
    )


__all__ = [
    "archive_professional",
    "create_professional",
    "get_professional",
    "link_professional_membership",
    "list_professionals",
    "restore_professional",
    "router",
    "unlink_professional_membership",
    "update_professional",
]
