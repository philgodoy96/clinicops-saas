from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.db.models import metadata
from clinicops.db.session import get_engine
from clinicops.patients.enums import PatientStatus
from clinicops.patients.models import Patient
from clinicops.tenancy.models import Tenant


@pytest.fixture
def db_session() -> Iterator[Session]:
    session = Session(get_engine())
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _tenant(session: Session) -> Tenant:
    tenant = Tenant(name=f"Patient Persistence Clinic {uuid4().hex}")
    session.add(tenant)
    session.flush()
    return tenant


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def test_patient_model_is_registered_in_database_metadata() -> None:
    assert "patients" in metadata.tables
    assert metadata.tables["patients"] is Patient.__table__


def test_patient_persists_with_expected_defaults(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    patient = Patient(
        tenant_id=tenant.id,
        full_name="Jordan Lee",
    )
    db_session.add(patient)
    db_session.flush()
    db_session.refresh(patient)

    assert isinstance(patient.id, UUID)
    assert patient.tenant_id == tenant.id
    assert patient.full_name == "Jordan Lee"
    assert patient.date_of_birth is None
    assert patient.email is None
    assert patient.phone is None
    assert patient.external_reference is None
    assert patient.status is PatientStatus.ACTIVE
    assert patient.version == 1
    assert patient.created_at.tzinfo is not None
    assert patient.updated_at.tzinfo is not None


def test_patient_accepts_optional_profile_fields(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    patient = Patient(
        tenant_id=tenant.id,
        full_name="Morgan Ellis",
        email="morgan.ellis@example.com",
        phone="+1-202-555-0172",
        external_reference="LEGACY-1001",
    )
    db_session.add(patient)
    db_session.flush()

    assert patient.email == "morgan.ellis@example.com"
    assert patient.phone == "+1-202-555-0172"
    assert patient.external_reference == "LEGACY-1001"


def test_patient_rejects_blank_full_name(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    db_session.add(
        Patient(
            tenant_id=tenant.id,
            full_name="   ",
        )
    )

    with pytest.raises(IntegrityError) as error:
        db_session.flush()

    assert _constraint_name(error.value) == "ck_patients_full_name_not_blank"


def test_patient_rejects_non_positive_version(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    db_session.add(
        Patient(
            tenant_id=tenant.id,
            full_name="Taylor Morgan",
            version=0,
        )
    )

    with pytest.raises(IntegrityError) as error:
        db_session.flush()

    assert _constraint_name(error.value) == "ck_patients_version_positive"


def test_external_reference_is_unique_within_tenant(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    db_session.add(
        Patient(
            tenant_id=tenant.id,
            full_name="Casey Adams",
            external_reference="LEGACY-2001",
        )
    )
    db_session.flush()

    db_session.add(
        Patient(
            tenant_id=tenant.id,
            full_name="Riley Adams",
            external_reference="LEGACY-2001",
        )
    )

    with pytest.raises(IntegrityError) as error:
        db_session.flush()

    assert _constraint_name(error.value) == "uq_patients_tenant_external_reference"


def test_external_reference_can_repeat_across_tenants(
    db_session: Session,
) -> None:
    first_tenant = _tenant(db_session)
    second_tenant = _tenant(db_session)

    db_session.add_all(
        [
            Patient(
                tenant_id=first_tenant.id,
                full_name="Avery Parker",
                external_reference="LEGACY-3001",
            ),
            Patient(
                tenant_id=second_tenant.id,
                full_name="Avery Parker",
                external_reference="LEGACY-3001",
            ),
        ]
    )

    db_session.flush()


def test_external_reference_allows_multiple_nulls_within_tenant(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    db_session.add_all(
        [
            Patient(
                tenant_id=tenant.id,
                full_name="Jamie Brooks",
            ),
            Patient(
                tenant_id=tenant.id,
                full_name="Cameron Brooks",
            ),
        ]
    )

    db_session.flush()


def test_patient_tenant_foreign_key_restricts_tenant_deletion(
    db_session: Session,
) -> None:
    tenant = _tenant(db_session)
    db_session.add(
        Patient(
            tenant_id=tenant.id,
            full_name="Reese Sullivan",
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError) as error:
        db_session.execute(
            text("DELETE FROM tenants WHERE id = :tenant_id"),
            {"tenant_id": tenant.id},
        )

    assert _constraint_name(error.value) == "fk_patients_tenant_id_tenants"


def test_patient_table_has_expected_constraints_and_indexes(
    db_session: Session,
) -> None:
    inspector = inspect(db_session.connection())

    check_constraint_names = {
        constraint["name"] for constraint in inspector.get_check_constraints("patients")
    }
    index_names = {index["name"] for index in inspector.get_indexes("patients")}
    foreign_keys = inspector.get_foreign_keys("patients")

    assert {
        "ck_patients_full_name_not_blank",
        "ck_patients_version_positive",
    }.issubset(check_constraint_names)
    assert {
        "ix_patients_tenant_timeline",
        "ix_patients_tenant_status_timeline",
        "uq_patients_tenant_external_reference",
    }.issubset(index_names)
    assert any(
        foreign_key["name"] == "fk_patients_tenant_id_tenants"
        and foreign_key["referred_table"] == "tenants"
        and foreign_key["options"].get("ondelete") == "RESTRICT"
        for foreign_key in foreign_keys
    )


def test_patient_status_enum_contains_only_supported_values(
    db_session: Session,
) -> None:
    values = (
        db_session.execute(
            text(
                """
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = 'patient_status'
            ORDER BY enumsortorder
            """
            )
        )
        .scalars()
        .all()
    )

    assert values == ["active", "archived"]
