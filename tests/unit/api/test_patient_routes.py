from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from clinicops.api.v1.patients import (
    archive_patient,
    create_patient,
    get_patient,
    list_patients,
    restore_patient,
    router,
    update_patient,
)
from clinicops.audit.enums import AuditSource
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.patients.contracts import (
    ArchivedPatient,
    ArchivePatientCommand,
    CreatedPatient,
    CreatePatientCommand,
    GetPatientCommand,
    PatientRecord,
    RestoredPatient,
    RestorePatientCommand,
    UpdatedPatient,
    UpdatePatientCommand,
)
from clinicops.patients.enums import (
    PatientListStatus,
    PatientMutableField,
    PatientStatus,
)
from clinicops.patients.exceptions import (
    PatientInvalidDateOfBirthError,
)
from clinicops.patients.schemas import (
    PatientCreateRequest,
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
    ListedPatients,
    ListPatientsQuery,
    ListPatientsService,
)
from clinicops.patients.services.restore_patient import (
    RestorePatientService,
)
from clinicops.patients.services.update_patient import (
    UpdatePatientService,
)
from clinicops.tenancy.models import TenantRole

_REQUEST_ID = "request-patient-api"
_CORRELATION_ID = "correlation-patient-api"


@pytest.fixture(autouse=True)
def patch_patient_route_request_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clinicops.api.v1.patients.get_request_id",
        lambda: _REQUEST_ID,
    )
    monkeypatch.setattr(
        "clinicops.api.v1.patients.get_correlation_id",
        lambda: _CORRELATION_ID,
    )


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
        return self.result


class FailingService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self, _command: object) -> object:
        raise self.error


def _context(
    *,
    tenant_id: UUID,
    permission: TenantPermission,
) -> AuthorizedTenantContext:
    return AuthorizedTenantContext(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid4(),
        role=TenantRole.OWNER,
        granted_permission=permission,
    )


def _patient(
    *,
    tenant_id: UUID,
    patient_id: UUID | None = None,
    status: PatientStatus = PatientStatus.ACTIVE,
    version: int = 1,
) -> PatientRecord:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    return PatientRecord(
        id=patient_id or uuid4(),
        tenant_id=tenant_id,
        full_name="Jordan Lee",
        date_of_birth=None,
        email="jordan@example.com",
        phone=None,
        external_reference=None,
        status=status,
        version=version,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _assert_http_audit_context(
    command: (
        CreatePatientCommand | UpdatePatientCommand | ArchivePatientCommand | RestorePatientCommand
    ),
    *,
    context: AuthorizedTenantContext,
) -> None:
    audit_context = command.audit_context
    assert audit_context.actor.user_id == context.user_id
    assert audit_context.actor.role == context.role.value
    assert audit_context.source is AuditSource.HTTP
    assert audit_context.request_id == _REQUEST_ID
    assert audit_context.correlation_id == _CORRELATION_ID


def test_create_route_uses_authorized_tenant_and_commits_once() -> None:
    tenant_id = uuid4()
    patient = _patient(tenant_id=tenant_id)
    service = RecordingService(CreatedPatient(patient=patient))
    session = RecordingSession()
    context = _context(
        tenant_id=tenant_id,
        permission=TenantPermission.PATIENT_CREATE,
    )

    response = create_patient(
        PatientCreateRequest(full_name="Jordan Lee"),
        context,
        cast(Session, session),
        cast(CreatePatientService, service),
    )

    command = service.commands[0]
    assert isinstance(command, CreatePatientCommand)
    assert command.tenant_id == tenant_id
    _assert_http_audit_context(command, context=context)
    assert response.id == patient.id
    assert session.commit_count == 1


def test_list_route_passes_filters_without_committing() -> None:
    tenant_id = uuid4()
    patient = _patient(tenant_id=tenant_id)
    service = RecordingService(
        ListedPatients(
            items=(patient,),
            next_cursor="next-page",
        )
    )

    response = list_patients(
        _context(
            tenant_id=tenant_id,
            permission=TenantPermission.PATIENT_READ,
        ),
        cast(ListPatientsService, service),
        limit=25,
        status_filter=PatientListStatus.ALL,
        search="Jordan",
        cursor="opaque",
    )

    query = service.commands[0]
    assert isinstance(query, ListPatientsQuery)
    assert query.tenant_id == tenant_id
    assert query.limit == 25
    assert query.status is PatientListStatus.ALL
    assert query.search == "Jordan"
    assert query.cursor == "opaque"
    assert response.next_cursor == "next-page"
    assert [item.id for item in response.items] == [patient.id]


def test_get_route_uses_authorized_tenant() -> None:
    tenant_id = uuid4()
    patient = _patient(tenant_id=tenant_id)
    service = RecordingService(patient)

    response = get_patient(
        patient.id,
        _context(
            tenant_id=tenant_id,
            permission=TenantPermission.PATIENT_READ,
        ),
        cast(GetPatientService, service),
    )

    command = service.commands[0]
    assert isinstance(command, GetPatientCommand)
    assert command.tenant_id == tenant_id
    assert command.patient_id == patient.id
    assert response.id == patient.id


def test_update_route_tracks_explicit_null_and_commits_once() -> None:
    tenant_id = uuid4()
    patient = _patient(
        tenant_id=tenant_id,
        version=4,
    )
    service = RecordingService(
        UpdatedPatient(
            patient=patient,
            changed_fields=(PatientMutableField.EMAIL,),
        )
    )
    session = RecordingSession()
    context = _context(
        tenant_id=tenant_id,
        permission=TenantPermission.PATIENT_UPDATE,
    )

    response = update_patient(
        PatientUpdateRequest(
            expected_version=3,
            email=None,
        ),
        patient.id,
        context,
        cast(Session, session),
        cast(UpdatePatientService, service),
    )

    command = service.commands[0]
    assert isinstance(command, UpdatePatientCommand)
    assert command.fields_to_update == frozenset({PatientMutableField.EMAIL})
    assert command.email is None
    _assert_http_audit_context(command, context=context)
    assert response.version == 4
    assert session.commit_count == 1


def test_archive_and_restore_routes_commit_each_transition() -> None:
    tenant_id = uuid4()
    patient_id = uuid4()
    archived = _patient(
        tenant_id=tenant_id,
        patient_id=patient_id,
        status=PatientStatus.ARCHIVED,
        version=2,
    )
    restored = _patient(
        tenant_id=tenant_id,
        patient_id=patient_id,
        status=PatientStatus.ACTIVE,
        version=3,
    )
    archive_service = RecordingService(ArchivedPatient(patient=archived))
    restore_service = RecordingService(RestoredPatient(patient=restored))
    archive_session = RecordingSession()
    restore_session = RecordingSession()
    archive_context = _context(
        tenant_id=tenant_id,
        permission=TenantPermission.PATIENT_ARCHIVE,
    )
    restore_context = _context(
        tenant_id=tenant_id,
        permission=TenantPermission.PATIENT_RESTORE,
    )

    archive_response = archive_patient(
        PatientVersionRequest(expected_version=1),
        patient_id,
        archive_context,
        cast(Session, archive_session),
        cast(ArchivePatientService, archive_service),
    )
    restore_response = restore_patient(
        PatientVersionRequest(expected_version=2),
        patient_id,
        restore_context,
        cast(Session, restore_session),
        cast(RestorePatientService, restore_service),
    )

    archive_command = archive_service.commands[0]
    restore_command = restore_service.commands[0]
    assert isinstance(archive_command, ArchivePatientCommand)
    assert isinstance(restore_command, RestorePatientCommand)
    _assert_http_audit_context(
        archive_command,
        context=archive_context,
    )
    _assert_http_audit_context(
        restore_command,
        context=restore_context,
    )
    assert archive_response.status is PatientStatus.ARCHIVED
    assert restore_response.status is PatientStatus.ACTIVE
    assert archive_session.commit_count == 1
    assert restore_session.commit_count == 1


def test_mutation_route_does_not_commit_when_service_fails() -> None:
    tenant_id = uuid4()
    session = RecordingSession()
    failure = PatientInvalidDateOfBirthError()

    try:
        create_patient(
            PatientCreateRequest(full_name="Jordan Lee"),
            _context(
                tenant_id=tenant_id,
                permission=TenantPermission.PATIENT_CREATE,
            ),
            cast(Session, session),
            cast(CreatePatientService, FailingService(failure)),
        )
    except PatientInvalidDateOfBirthError as error:
        assert error is failure
    else:
        raise AssertionError("The service failure must propagate.")

    assert session.commit_count == 0


def test_openapi_exposes_only_approved_patient_operations() -> None:
    application = FastAPI()
    application.include_router(router)
    schema = application.openapi()
    paths = schema["paths"]

    collection_path = "/tenants/{tenant_id}/patients"
    item_path = "/tenants/{tenant_id}/patients/{patient_id}"
    archive_path = "/tenants/{tenant_id}/patients/{patient_id}/archive"
    restore_path = "/tenants/{tenant_id}/patients/{patient_id}/restore"

    assert set(paths[collection_path]) == {
        "get",
        "post",
    }
    assert set(paths[item_path]) == {
        "get",
        "patch",
    }
    assert set(paths[archive_path]) == {"post"}
    assert set(paths[restore_path]) == {"post"}
    assert "delete" not in paths[item_path]
    assert paths[collection_path]["post"]["responses"]["201"] is not None
