from typing import NoReturn

from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.patients.contracts import (
    PatientRecord,
    RestorePatientCommand,
    RestoredPatient,
)
from clinicops.patients.enums import PatientStatus
from clinicops.patients.exceptions import (
    PatientNotArchivedError,
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)


class RestorePatientService:
    """Restore an archived patient using optimistic concurrency."""

    def __init__(
        self,
        repository: PatientRepository,
        session: Session,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._session = session
        self._audit_recorder = (
            audit_recorder if audit_recorder is not None else SqlAlchemyAuditRecorder()
        )

    def execute(
        self,
        command: RestorePatientCommand,
    ) -> RestoredPatient:
        """Restore a tenant-owned patient at the observed version."""

        current = self._repository.get_by_id_for_tenant(
            tenant_id=command.tenant_id,
            patient_id=command.patient_id,
        )
        if current is None:
            raise PatientNotFoundError

        _require_current_version(
            current,
            expected_version=command.expected_version,
        )
        _require_archived_patient(current)

        restored = self._repository.restore_for_tenant(
            tenant_id=command.tenant_id,
            patient_id=command.patient_id,
            expected_version=command.expected_version,
        )
        if restored is None:
            _raise_failed_restore(
                repository=self._repository,
                command=command,
            )

        audit_context = command.audit_context
        self._audit_recorder.record(
            self._session,
            RecordAuditLogCommand(
                tenant_id=command.tenant_id,
                actor=audit_context.actor,
                source=audit_context.source,
                action=AuditAction.PATIENT_RESTORED.value,
                resource_type=AuditResourceType.PATIENT.value,
                resource_id=str(restored.id),
                correlation_id=audit_context.correlation_id,
                metadata_version=1,
                metadata={
                    "previous_status": PatientStatus.ARCHIVED.value,
                    "new_status": PatientStatus.ACTIVE.value,
                    "version": restored.version,
                },
                idempotency_key=(f"patient-restored:{restored.id}:{restored.version}"),
                request_id=audit_context.request_id,
            ),
        )

        return RestoredPatient(patient=restored)


def _require_current_version(
    patient: PatientRecord,
    *,
    expected_version: int,
) -> None:
    if patient.version != expected_version:
        raise PatientVersionConflictError


def _require_archived_patient(patient: PatientRecord) -> None:
    if patient.status is PatientStatus.ACTIVE:
        raise PatientNotArchivedError


def _raise_failed_restore(
    *,
    repository: PatientRepository,
    command: RestorePatientCommand,
) -> NoReturn:
    latest = repository.get_by_id_for_tenant(
        tenant_id=command.tenant_id,
        patient_id=command.patient_id,
    )
    if latest is None:
        raise PatientNotFoundError

    _require_current_version(
        latest,
        expected_version=command.expected_version,
    )
    _require_archived_patient(latest)

    raise PatientVersionConflictError


__all__ = ["RestorePatientService"]
