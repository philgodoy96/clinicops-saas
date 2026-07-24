import json
from collections.abc import Iterator
from typing import NoReturn, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.models import AuditLogEntry
from clinicops.audit.recording import (
    AuditRecorder,
    SqlAlchemyAuditRecorder,
)
from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.patients.contracts import (
    ArchivePatientCommand,
    CreatePatientCommand,
    RestorePatientCommand,
    UpdatePatientCommand,
)
from clinicops.patients.enums import PatientMutableField
from clinicops.patients.models import Patient
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.archive_patient import (
    ArchivePatientService,
)
from clinicops.patients.services.create_patient import (
    CreatePatientService,
)
from clinicops.patients.services.restore_patient import (
    RestorePatientService,
)
from clinicops.patients.services.update_patient import (
    UpdatePatientService,
)
from clinicops.tenancy.models import Tenant

_FORBIDDEN_METADATA_KEYS = {
    "date_of_birth",
    "email",
    "external_reference",
    "full_name",
    "phone",
}


class SimulatedAuditRecordingError(RuntimeError):
    """Raised after an audit entry has been flushed for rollback testing."""


class PersistThenFailAuditRecorder:
    def __init__(self) -> None:
        self.commands: list[RecordAuditLogCommand] = []

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> NoReturn:
        self.commands.append(command)
        SqlAlchemyAuditRecorder().record(session, command)
        raise SimulatedAuditRecordingError("Simulated audit recording failure.")


@pytest.fixture
def committed_tenant() -> Iterator[tuple[UUID, UUID]]:
    setup_session = Session(get_engine())
    tenant = Tenant(name=f"Patient Audit Clinic {uuid4().hex}")
    actor = User(email=f"patient-audit-actor-{uuid4().hex}@example.com")
    setup_session.add_all([tenant, actor])
    setup_session.commit()
    tenant_id = tenant.id
    actor_user_id = actor.id
    setup_session.close()

    try:
        yield tenant_id, actor_user_id
    finally:
        cleanup_session = Session(get_engine())
        try:
            cleanup_session.execute(
                delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id)
            )
            cleanup_session.execute(delete(Patient).where(Patient.tenant_id == tenant_id))
            cleanup_session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            cleanup_session.execute(delete(User).where(User.id == actor_user_id))
            cleanup_session.commit()
        finally:
            cleanup_session.close()


def _audit_context(*, user_id: UUID) -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=user_id,
        role="owner",
        request_id="request-patient-audit",
        correlation_id="correlation-patient-audit",
    )


def test_patient_mutations_and_audit_entries_commit_together(
    committed_tenant: tuple[UUID, UUID],
) -> None:
    tenant_id, actor_user_id = committed_tenant
    session = Session(get_engine())
    context = _audit_context(user_id=actor_user_id)

    try:
        repository = PatientRepository(session)

        created = CreatePatientService(
            repository,
            session,
        ).execute(
            CreatePatientCommand(
                tenant_id=tenant_id,
                full_name="Jordan Lee",
                email="jordan@example.com",
                phone="+1-202-555-0184",
                external_reference="LEGACY-100",
                audit_context=context,
            )
        )

        updated = UpdatePatientService(
            repository,
            session,
        ).execute(
            UpdatePatientCommand(
                tenant_id=tenant_id,
                patient_id=created.patient.id,
                expected_version=1,
                fields_to_update=frozenset(
                    {
                        PatientMutableField.EMAIL,
                        PatientMutableField.PHONE,
                    }
                ),
                email="new@example.com",
                phone=None,
                audit_context=context,
            )
        )

        archived = ArchivePatientService(
            repository,
            session,
        ).execute(
            ArchivePatientCommand(
                tenant_id=tenant_id,
                patient_id=created.patient.id,
                expected_version=2,
                audit_context=context,
            )
        )

        restored = RestorePatientService(
            repository,
            session,
        ).execute(
            RestorePatientCommand(
                tenant_id=tenant_id,
                patient_id=created.patient.id,
                expected_version=3,
                audit_context=context,
            )
        )

        assert created.patient.version == 1
        assert updated.patient.version == 2
        assert archived.patient.version == 3
        assert restored.patient.version == 4
        assert session.in_transaction() is True

        session.commit()
    finally:
        session.rollback()
        session.close()

    verification_session = Session(get_engine())
    try:
        patient = verification_session.scalar(
            select(Patient).where(
                Patient.id == created.patient.id,
                Patient.tenant_id == tenant_id,
            )
        )
        entries = list(
            verification_session.scalars(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == tenant_id,
                    AuditLogEntry.resource_id == str(created.patient.id),
                )
            ).all()
        )
    finally:
        verification_session.close()

    assert patient is not None
    assert patient.version == 4

    entries_by_action = {entry.action: entry for entry in entries}
    assert set(entries_by_action) == {
        AuditAction.PATIENT_CREATED.value,
        AuditAction.PATIENT_UPDATED.value,
        AuditAction.PATIENT_ARCHIVED.value,
        AuditAction.PATIENT_RESTORED.value,
    }

    created_entry = entries_by_action[AuditAction.PATIENT_CREATED.value]
    updated_entry = entries_by_action[AuditAction.PATIENT_UPDATED.value]
    archived_entry = entries_by_action[AuditAction.PATIENT_ARCHIVED.value]
    restored_entry = entries_by_action[AuditAction.PATIENT_RESTORED.value]

    assert created_entry.resource_type == (AuditResourceType.PATIENT.value)
    assert created_entry.event_metadata == {
        "status": "active",
        "version": 1,
    }
    assert updated_entry.event_metadata == {
        "changed_fields": ["email", "phone"],
        "version": 2,
    }
    assert archived_entry.event_metadata == {
        "new_status": "archived",
        "previous_status": "active",
        "version": 3,
    }
    assert restored_entry.event_metadata == {
        "new_status": "active",
        "previous_status": "archived",
        "version": 4,
    }

    for entry in entries:
        assert entry.actor_user_id == context.actor.user_id
        assert entry.actor_role == context.actor.role
        assert entry.source == context.source.value
        assert entry.request_id == context.request_id
        assert entry.correlation_id == context.correlation_id
        assert not (_FORBIDDEN_METADATA_KEYS & set(entry.event_metadata))

    serialized_metadata = json.dumps(
        [entry.event_metadata for entry in entries],
        sort_keys=True,
    )
    assert "jordan@example.com" not in serialized_metadata
    assert "new@example.com" not in serialized_metadata
    assert "+1-202-555-0184" not in serialized_metadata
    assert "LEGACY-100" not in serialized_metadata
    assert "Jordan Lee" not in serialized_metadata


def test_audit_failure_rolls_back_patient_and_audit_entry(
    committed_tenant: tuple[UUID, UUID],
) -> None:
    tenant_id, actor_user_id = committed_tenant
    session = Session(get_engine())
    recorder = PersistThenFailAuditRecorder()
    context = _audit_context(user_id=actor_user_id)

    try:
        service = CreatePatientService(
            PatientRepository(session),
            session,
            cast(AuditRecorder, recorder),
        )

        with pytest.raises(SimulatedAuditRecordingError):
            service.execute(
                CreatePatientCommand(
                    tenant_id=tenant_id,
                    full_name="Rollback Patient",
                    audit_context=context,
                )
            )

        assert len(recorder.commands) == 1
        patient_id = UUID(recorder.commands[0].resource_id)
        assert session.in_transaction() is True
        session.rollback()
    finally:
        session.close()

    verification_session = Session(get_engine())
    try:
        patient = verification_session.scalar(select(Patient).where(Patient.id == patient_id))
        audit_entry = verification_session.scalar(
            select(AuditLogEntry).where(AuditLogEntry.resource_id == str(patient_id))
        )
    finally:
        verification_session.close()

    assert patient is None
    assert audit_entry is None


def test_patient_audit_idempotency_keys_are_versioned(
    committed_tenant: tuple[UUID, UUID],
) -> None:
    tenant_id, actor_user_id = committed_tenant
    session = Session(get_engine())
    context = _audit_context(user_id=actor_user_id)

    try:
        repository = PatientRepository(session)
        created = CreatePatientService(
            repository,
            session,
        ).execute(
            CreatePatientCommand(
                tenant_id=tenant_id,
                full_name="Versioned Audit Patient",
                audit_context=context,
            )
        )
        updated = UpdatePatientService(
            repository,
            session,
        ).execute(
            UpdatePatientCommand(
                tenant_id=tenant_id,
                patient_id=created.patient.id,
                expected_version=1,
                fields_to_update=frozenset({PatientMutableField.PHONE}),
                phone="+1-202-555-0101",
                audit_context=context,
            )
        )
        archived = ArchivePatientService(
            repository,
            session,
        ).execute(
            ArchivePatientCommand(
                tenant_id=tenant_id,
                patient_id=created.patient.id,
                expected_version=2,
                audit_context=context,
            )
        )
        session.commit()
    finally:
        session.rollback()
        session.close()

    verification_session = Session(get_engine())
    try:
        entries = list(
            verification_session.scalars(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == tenant_id,
                    AuditLogEntry.resource_id == str(created.patient.id),
                )
            ).all()
        )
    finally:
        verification_session.close()

    keys = {entry.idempotency_key for entry in entries}
    assert keys == {
        f"patient-created:{created.patient.id}",
        (f"patient-updated:{updated.patient.id}:{updated.patient.version}"),
        (f"patient-archived:{archived.patient.id}:{archived.patient.version}"),
    }
