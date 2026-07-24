from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.models import AuditLogEntry
from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.patients.contracts import (
    ArchivePatientCommand,
    RestorePatientCommand,
)
from clinicops.patients.enums import PatientStatus
from clinicops.patients.exceptions import (
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.models import Patient
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.archive_patient import (
    ArchivePatientService,
)
from clinicops.patients.services.restore_patient import (
    RestorePatientService,
)
from clinicops.tenancy.models import Tenant


def _audit_context(*, user_id: UUID) -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=user_id,
        role="owner",
        request_id="request-patient-lifecycle",
        correlation_id="correlation-patient-lifecycle",
    )


@pytest.fixture
def persisted_patient() -> Iterator[tuple[UUID, UUID, UUID]]:
    setup_session = Session(get_engine())
    tenant = Tenant(name=f"Patient Lifecycle Clinic {uuid4().hex}")
    actor = User(email=f"patient-lifecycle-actor-{uuid4().hex}@example.com")
    patient = Patient(
        tenant=tenant,
        full_name="Jordan Lee",
    )
    setup_session.add_all([tenant, actor, patient])
    setup_session.commit()

    tenant_id = tenant.id
    patient_id = patient.id
    actor_user_id = actor.id
    setup_session.close()

    try:
        yield tenant_id, patient_id, actor_user_id
    finally:
        cleanup_session = Session(get_engine())
        try:
            cleanup_session.execute(
                delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id)
            )
            cleanup_session.execute(delete(Patient).where(Patient.id == patient_id))
            cleanup_session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            cleanup_session.execute(delete(User).where(User.id == actor_user_id))
            cleanup_session.commit()
        finally:
            cleanup_session.close()


def test_patient_can_be_archived_and_restored_without_deletion(
    persisted_patient: tuple[UUID, UUID, UUID],
) -> None:
    tenant_id, patient_id, actor_user_id = persisted_patient
    session = Session(get_engine())
    context = _audit_context(user_id=actor_user_id)

    try:
        repository = PatientRepository(session)
        archived = ArchivePatientService(repository, session).execute(
            ArchivePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=1,
                audit_context=context,
            )
        )

        assert archived.patient.status is PatientStatus.ARCHIVED
        assert archived.patient.version == 2

        restored = RestorePatientService(repository, session).execute(
            RestorePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=2,
                audit_context=context,
            )
        )

        assert restored.patient.id == patient_id
        assert restored.patient.status is PatientStatus.ACTIVE
        assert restored.patient.version == 3
        assert session.in_transaction() is True

        session.commit()
    finally:
        session.rollback()
        session.close()

    verification_session = Session(get_engine())
    try:
        persisted = PatientRepository(verification_session).get_by_id_for_tenant(
            tenant_id=tenant_id,
            patient_id=patient_id,
        )
    finally:
        verification_session.close()

    assert persisted is not None
    assert persisted.status is PatientStatus.ACTIVE
    assert persisted.version == 3


def test_stale_archive_command_cannot_repeat_transition(
    persisted_patient: tuple[UUID, UUID, UUID],
) -> None:
    tenant_id, patient_id, actor_user_id = persisted_patient
    first_session = Session(get_engine())
    second_session = Session(get_engine())
    context = _audit_context(user_id=actor_user_id)

    try:
        first_service = ArchivePatientService(
            PatientRepository(first_session),
            first_session,
        )
        second_service = ArchivePatientService(
            PatientRepository(second_session),
            second_session,
        )

        first_result = first_service.execute(
            ArchivePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=1,
                audit_context=context,
            )
        )
        first_session.commit()

        assert first_result.patient.version == 2

        with pytest.raises(PatientVersionConflictError):
            second_service.execute(
                ArchivePatientCommand(
                    tenant_id=tenant_id,
                    patient_id=patient_id,
                    expected_version=1,
                    audit_context=context,
                )
            )
    finally:
        first_session.rollback()
        first_session.close()
        second_session.rollback()
        second_session.close()


def test_cross_tenant_lifecycle_command_is_not_found(
    persisted_patient: tuple[UUID, UUID, UUID],
) -> None:
    _, patient_id, actor_user_id = persisted_patient
    session = Session(get_engine())
    foreign_tenant = Tenant(name=f"Foreign Lifecycle Clinic {uuid4().hex}")
    session.add(foreign_tenant)
    session.commit()
    foreign_tenant_id = foreign_tenant.id

    try:
        service = ArchivePatientService(PatientRepository(session), session)

        with pytest.raises(PatientNotFoundError):
            service.execute(
                ArchivePatientCommand(
                    tenant_id=foreign_tenant_id,
                    patient_id=patient_id,
                    expected_version=1,
                    audit_context=_audit_context(user_id=actor_user_id),
                )
            )
    finally:
        session.rollback()
        session.execute(delete(Tenant).where(Tenant.id == foreign_tenant_id))
        session.commit()
        session.close()
