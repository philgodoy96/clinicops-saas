from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.db.models import metadata
from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.models import Professional
from clinicops.tenancy.models import Membership, Tenant, TenantRole


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _create_tenant(
    session: Session,
    *,
    name: str,
) -> Tenant:
    tenant = Tenant(name=f"{name} {uuid4().hex[:8]}")
    session.add(tenant)
    session.flush()
    return tenant


def _create_membership(
    session: Session,
    *,
    tenant: Tenant,
    role: TenantRole = TenantRole.STAFF,
) -> Membership:
    user = User(email=f"professional-{uuid4().hex}@example.com")
    membership = Membership(
        tenant=tenant,
        user=user,
        role=role,
    )
    session.add(membership)
    session.flush()
    return membership


def test_professional_persists_with_lifecycle_defaults(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Northstar Health Clinic")
    professional = Professional(
        tenant_id=tenant.id,
        full_name="Morgan Reed",
    )

    db_session.add(professional)
    db_session.flush()
    db_session.refresh(professional)

    assert professional.id is not None
    assert professional.tenant_id == tenant.id
    assert professional.membership_id is None
    assert professional.status is ProfessionalStatus.ACTIVE
    assert professional.version == 1
    assert professional.created_at.tzinfo is not None
    assert professional.updated_at.tzinfo is not None


def test_professional_persists_optional_profile_and_membership_fields(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Lakeside Dental Clinic")
    membership = _create_membership(db_session, tenant=tenant)
    professional = Professional(
        tenant_id=tenant.id,
        membership_id=membership.id,
        full_name="Alex Morgan",
        specialty="Dentistry",
        registration_number="DDS-48291",
        registration_region="CA",
        email="alex.morgan@example.com",
        phone="+1-202-555-0130",
        external_reference="PROVIDER-100",
    )

    db_session.add(professional)
    db_session.flush()
    db_session.refresh(professional)

    assert professional.membership_id == membership.id
    assert professional.specialty == "Dentistry"
    assert professional.registration_number == "DDS-48291"
    assert professional.registration_region == "CA"
    assert professional.external_reference == "PROVIDER-100"


def test_professional_rejects_blank_full_name(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Harbor Medical Center")
    db_session.add(
        Professional(
            tenant_id=tenant.id,
            full_name="   ",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_professional_rejects_non_positive_version(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Summit Care Clinic")
    db_session.add(
        Professional(
            tenant_id=tenant.id,
            full_name="Taylor Jordan",
            version=0,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_external_reference_is_unique_within_tenant(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Cedar Health Clinic")
    db_session.add_all(
        [
            Professional(
                tenant_id=tenant.id,
                full_name="Jamie West",
                external_reference="PROVIDER-200",
            ),
            Professional(
                tenant_id=tenant.id,
                full_name="Casey North",
                external_reference="PROVIDER-200",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_external_reference_may_repeat_across_tenants(
    db_session: Session,
) -> None:
    first_tenant = _create_tenant(db_session, name="First Clinic")
    second_tenant = _create_tenant(db_session, name="Second Clinic")
    db_session.add_all(
        [
            Professional(
                tenant_id=first_tenant.id,
                full_name="Robin Gray",
                external_reference="PROVIDER-300",
            ),
            Professional(
                tenant_id=second_tenant.id,
                full_name="Robin Gray",
                external_reference="PROVIDER-300",
            ),
        ]
    )

    db_session.flush()


def test_multiple_professionals_may_have_null_external_reference_and_membership(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Maple Health Clinic")
    db_session.add_all(
        [
            Professional(
                tenant_id=tenant.id,
                full_name="Avery Green",
            ),
            Professional(
                tenant_id=tenant.id,
                full_name="Riley Stone",
            ),
        ]
    )

    db_session.flush()


def test_membership_may_link_to_only_one_professional(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Evergreen Clinic")
    membership = _create_membership(db_session, tenant=tenant)
    db_session.add_all(
        [
            Professional(
                tenant_id=tenant.id,
                membership_id=membership.id,
                full_name="Drew Parker",
            ),
            Professional(
                tenant_id=tenant.id,
                membership_id=membership.id,
                full_name="Quinn Baker",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_professional_rejects_membership_from_another_tenant(
    db_session: Session,
) -> None:
    owning_tenant = _create_tenant(db_session, name="Owning Clinic")
    foreign_tenant = _create_tenant(db_session, name="Foreign Clinic")
    foreign_membership = _create_membership(
        db_session,
        tenant=foreign_tenant,
    )
    db_session.add(
        Professional(
            tenant_id=owning_tenant.id,
            membership_id=foreign_membership.id,
            full_name="Skyler Lane",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_professional_model_is_registered_in_database_metadata() -> None:
    assert metadata.tables["professionals"] is Professional.__table__


def test_professional_indexes_and_membership_support_constraint_exist(
    db_session: Session,
) -> None:
    inspector = inspect(db_session.get_bind())

    professional_index_names = {index["name"] for index in inspector.get_indexes("professionals")}
    membership_unique_constraint_names = {
        constraint["name"] for constraint in inspector.get_unique_constraints("memberships")
    }

    assert {
        "ix_professionals_tenant_timeline",
        "ix_professionals_tenant_status_timeline",
        "uq_professionals_tenant_external_reference",
        "uq_professionals_membership_id",
    } <= professional_index_names
    assert "uq_memberships_tenant_id_id" in membership_unique_constraint_names


def test_professional_status_enum_contains_only_supported_values(
    db_session: Session,
) -> None:
    values = (
        db_session.execute(
            text(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                WHERE pg_type.typname = 'professional_status'
                ORDER BY enumsortorder
                """
            )
        )
        .scalars()
        .all()
    )

    assert values == ["active", "archived"]
