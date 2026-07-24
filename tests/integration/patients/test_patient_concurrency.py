from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.patients.contracts import UpdatePatientCommand
from clinicops.patients.enums import PatientMutableField
from clinicops.patients.exceptions import (
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.models import Patient
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.services.update_patient import (
    UpdatePatientService,
)
from clinicops.tenancy.models import Tenant


@pytest.fixture
def persisted_patient() -> Iterator[tuple[UUID, UUID]]:
    setup_session = Session(get_engine())
    tenant = Tenant(name=f"Patient Concurrency Clinic {uuid4().hex}")
    patient = Patient(
        tenant=tenant,
        full_name="Jordan Lee",
        email="initial@example.com",
    )
    setup_session.add(patient)
    setup_session.commit()

    tenant_id = tenant.id
    patient_id = patient.id
    setup_session.close()

    try:
        yield tenant_id, patient_id
    finally:
        cleanup_session = Session(get_engine())
        try:
            cleanup_session.execute(delete(Patient).where(Patient.id == patient_id))
            cleanup_session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            cleanup_session.commit()
        finally:
            cleanup_session.close()


def test_stale_writer_cannot_overwrite_committed_update(
    persisted_patient: tuple[UUID, UUID],
) -> None:
    tenant_id, patient_id = persisted_patient
    first_session = Session(get_engine())
    second_session = Session(get_engine())

    try:
        first_repository = PatientRepository(first_session)
        second_repository = PatientRepository(second_session)
        first_service = UpdatePatientService(first_repository)
        second_service = UpdatePatientService(second_repository)

        first_observation = first_repository.get_by_id_for_tenant(
            tenant_id=tenant_id,
            patient_id=patient_id,
        )
        second_observation = second_repository.get_by_id_for_tenant(
            tenant_id=tenant_id,
            patient_id=patient_id,
        )

        assert first_observation is not None
        assert second_observation is not None
        assert first_observation.version == 1
        assert second_observation.version == 1

        first_result = first_service.execute(
            UpdatePatientCommand(
                tenant_id=tenant_id,
                patient_id=patient_id,
                expected_version=first_observation.version,
                fields_to_update=frozenset({PatientMutableField.EMAIL}),
                email="first-writer@example.com",
            )
        )
        first_session.commit()

        assert first_result.patient.version == 2
        assert first_result.patient.email == "first-writer@example.com"

        with pytest.raises(PatientVersionConflictError):
            second_service.execute(
                UpdatePatientCommand(
                    tenant_id=tenant_id,
                    patient_id=patient_id,
                    expected_version=second_observation.version,
                    fields_to_update=frozenset({PatientMutableField.EMAIL}),
                    email="second-writer@example.com",
                )
            )

        second_session.rollback()

        verification_session = Session(get_engine())
        try:
            persisted = PatientRepository(verification_session).get_by_id_for_tenant(
                tenant_id=tenant_id,
                patient_id=patient_id,
            )
        finally:
            verification_session.close()

        assert persisted is not None
        assert persisted.version == 2
        assert persisted.email == "first-writer@example.com"
    finally:
        first_session.rollback()
        first_session.close()
        second_session.rollback()
        second_session.close()


def test_cross_tenant_update_is_reported_as_not_found(
    persisted_patient: tuple[UUID, UUID],
) -> None:
    tenant_id, patient_id = persisted_patient
    session = Session(get_engine())
    foreign_tenant = Tenant(name=f"Foreign Patient Clinic {uuid4().hex}")
    session.add(foreign_tenant)
    session.commit()
    foreign_tenant_id = foreign_tenant.id

    try:
        service = UpdatePatientService(PatientRepository(session))

        with pytest.raises(PatientNotFoundError):
            service.execute(
                UpdatePatientCommand(
                    tenant_id=foreign_tenant_id,
                    patient_id=patient_id,
                    expected_version=1,
                    fields_to_update=frozenset({PatientMutableField.EMAIL}),
                    email="foreign@example.com",
                )
            )
    finally:
        session.rollback()
        session.execute(delete(Tenant).where(Tenant.id == foreign_tenant_id))
        session.commit()
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
    assert persisted.version == 1
    assert persisted.email == "initial@example.com"
