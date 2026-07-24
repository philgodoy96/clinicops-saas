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
from clinicops.patients.contracts import (
    ArchivePatientCommand,
    CreatePatientCommand,
    GetPatientCommand,
    PatientRecord,
    RestorePatientCommand,
    UpdatePatientCommand,
)
from clinicops.patients.enums import (
    PatientListStatus,
    PatientMutableField,
)
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.schemas import (
    PatientCreateRequest,
    PatientListResponse,
    PatientResponse,
    PatientUpdateRequest,
    PatientVersionRequest,
)
from clinicops.patients.services.archive_patient import (
    ArchivePatientService,
)
from clinicops.patients.services.create_patient import (
    CreatePatientService,
)
from clinicops.patients.services.get_patient import (
    GetPatientService,
)
from clinicops.patients.services.list_patients import (
    ListPatientsQuery,
    ListPatientsService,
)
from clinicops.patients.services.restore_patient import (
    RestorePatientService,
)
from clinicops.patients.services.update_patient import (
    UpdatePatientService,
)
from clinicops.patients.validation import SEARCH_MAX_LENGTH

_MAX_CURSOR_LENGTH = 1024

router = APIRouter(
    prefix="/tenants",
    tags=["patients"],
)

PatientReadAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PATIENT_READ)),
]
PatientCreateAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PATIENT_CREATE)),
]
PatientUpdateAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PATIENT_UPDATE)),
]
PatientArchiveAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PATIENT_ARCHIVE)),
]
PatientRestoreAuthorizationDependency = Annotated[
    AuthorizedTenantContext,
    Depends(require_tenant_permission(TenantPermission.PATIENT_RESTORE)),
]


def get_create_patient_service(
    session: DatabaseSessionDependency,
) -> CreatePatientService:
    return CreatePatientService(
        PatientRepository(session),
        session,
    )


def get_get_patient_service(
    session: DatabaseSessionDependency,
) -> GetPatientService:
    return GetPatientService(PatientRepository(session))


def get_list_patients_service(
    session: DatabaseSessionDependency,
) -> ListPatientsService:
    return ListPatientsService(PatientRepository(session))


def get_update_patient_service(
    session: DatabaseSessionDependency,
) -> UpdatePatientService:
    return UpdatePatientService(
        PatientRepository(session),
        session,
    )


def get_archive_patient_service(
    session: DatabaseSessionDependency,
) -> ArchivePatientService:
    return ArchivePatientService(
        PatientRepository(session),
        session,
    )


def get_restore_patient_service(
    session: DatabaseSessionDependency,
) -> RestorePatientService:
    return RestorePatientService(
        PatientRepository(session),
        session,
    )


CreatePatientServiceDependency = Annotated[
    CreatePatientService,
    Depends(get_create_patient_service),
]
GetPatientServiceDependency = Annotated[
    GetPatientService,
    Depends(get_get_patient_service),
]
ListPatientsServiceDependency = Annotated[
    ListPatientsService,
    Depends(get_list_patients_service),
]
UpdatePatientServiceDependency = Annotated[
    UpdatePatientService,
    Depends(get_update_patient_service),
]
ArchivePatientServiceDependency = Annotated[
    ArchivePatientService,
    Depends(get_archive_patient_service),
]
RestorePatientServiceDependency = Annotated[
    RestorePatientService,
    Depends(get_restore_patient_service),
]


@router.post(
    "/{tenant_id}/patients",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a patient",
)
def create_patient(
    payload: PatientCreateRequest,
    context: PatientCreateAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: CreatePatientServiceDependency,
) -> PatientResponse:
    result = service.execute(
        CreatePatientCommand(
            tenant_id=context.tenant_id,
            full_name=payload.full_name,
            date_of_birth=payload.date_of_birth,
            email=payload.email,
            phone=payload.phone,
            external_reference=payload.external_reference,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _patient_response(result.patient)


@router.get(
    "/{tenant_id}/patients",
    response_model=PatientListResponse,
    status_code=status.HTTP_200_OK,
    summary="List patients",
)
def list_patients(
    context: PatientReadAuthorizationDependency,
    service: ListPatientsServiceDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status_filter: Annotated[
        PatientListStatus,
        Query(alias="status"),
    ] = PatientListStatus.ACTIVE,
    search: Annotated[
        str | None,
        Query(max_length=SEARCH_MAX_LENGTH),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(max_length=_MAX_CURSOR_LENGTH),
    ] = None,
) -> PatientListResponse:
    result = service.execute(
        ListPatientsQuery(
            tenant_id=context.tenant_id,
            limit=limit,
            status=status_filter,
            search=search,
            cursor=cursor,
        )
    )
    return PatientListResponse(
        items=[_patient_response(patient) for patient in result.items],
        next_cursor=result.next_cursor,
    )


@router.get(
    "/{tenant_id}/patients/{patient_id}",
    response_model=PatientResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a patient",
)
def get_patient(
    patient_id: UUID,
    context: PatientReadAuthorizationDependency,
    service: GetPatientServiceDependency,
) -> PatientResponse:
    patient = service.execute(
        GetPatientCommand(
            tenant_id=context.tenant_id,
            patient_id=patient_id,
        )
    )
    return _patient_response(patient)


@router.patch(
    "/{tenant_id}/patients/{patient_id}",
    response_model=PatientResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a patient",
)
def update_patient(
    payload: PatientUpdateRequest,
    patient_id: UUID,
    context: PatientUpdateAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: UpdatePatientServiceDependency,
) -> PatientResponse:
    fields_to_update = frozenset(
        PatientMutableField(field_name)
        for field_name in payload.model_fields_set
        if field_name != "expected_version"
    )
    result = service.execute(
        UpdatePatientCommand(
            tenant_id=context.tenant_id,
            patient_id=patient_id,
            expected_version=payload.expected_version,
            fields_to_update=fields_to_update,
            full_name=payload.full_name,
            date_of_birth=payload.date_of_birth,
            email=payload.email,
            phone=payload.phone,
            external_reference=payload.external_reference,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _patient_response(result.patient)


@router.post(
    "/{tenant_id}/patients/{patient_id}/archive",
    response_model=PatientResponse,
    status_code=status.HTTP_200_OK,
    summary="Archive a patient",
)
def archive_patient(
    payload: PatientVersionRequest,
    patient_id: UUID,
    context: PatientArchiveAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: ArchivePatientServiceDependency,
) -> PatientResponse:
    result = service.execute(
        ArchivePatientCommand(
            tenant_id=context.tenant_id,
            patient_id=patient_id,
            expected_version=payload.expected_version,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _patient_response(result.patient)


@router.post(
    "/{tenant_id}/patients/{patient_id}/restore",
    response_model=PatientResponse,
    status_code=status.HTTP_200_OK,
    summary="Restore a patient",
)
def restore_patient(
    payload: PatientVersionRequest,
    patient_id: UUID,
    context: PatientRestoreAuthorizationDependency,
    session: DatabaseSessionDependency,
    service: RestorePatientServiceDependency,
) -> PatientResponse:
    result = service.execute(
        RestorePatientCommand(
            tenant_id=context.tenant_id,
            patient_id=patient_id,
            expected_version=payload.expected_version,
            audit_context=_http_audit_context(context),
        )
    )
    session.commit()
    return _patient_response(result.patient)


def _patient_response(
    patient: PatientRecord,
) -> PatientResponse:
    return PatientResponse.model_validate(patient)


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
    "archive_patient",
    "create_patient",
    "get_patient",
    "list_patients",
    "restore_patient",
    "router",
    "update_patient",
]
