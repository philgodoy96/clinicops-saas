from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.professionals.contracts import (
    UnlinkProfessionalForMembershipRemovalCommand,
)
from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.models import Professional
from clinicops.professionals.services.unlink_professional_for_membership_removal import (
    UnlinkProfessionalForMembershipRemovalService,
)
from clinicops.tenancy.models import Membership, Tenant, TenantRole


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated PostgreSQL transaction."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _create_tenant_membership_and_professional(
    session: Session,
    *,
    professional_status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
) -> tuple[Tenant, Membership, Professional]:
    tenant = Tenant(name=f"Professional Unlink {uuid4().hex[:8]}")
    user = User(email=f"professional-unlink-{uuid4().hex}@example.com")
    membership = Membership(
        tenant=tenant,
        user=user,
        role=TenantRole.STAFF,
    )

    session.add_all([tenant, membership])
    session.flush()

    professional = Professional(
        tenant_id=tenant.id,
        membership_id=membership.id,
        full_name="Morgan Reed",
        status=professional_status,
    )
    session.add(professional)
    session.flush()
    session.refresh(professional)

    return tenant, membership, professional


@pytest.mark.parametrize(
    "professional_status",
    [
        ProfessionalStatus.ACTIVE,
        ProfessionalStatus.ARCHIVED,
    ],
)
def test_unlink_allows_membership_delete_and_preserves_professional(
    db_session: Session,
    professional_status: ProfessionalStatus,
) -> None:
    tenant, membership, professional = _create_tenant_membership_and_professional(
        db_session,
        professional_status=professional_status,
    )
    service = UnlinkProfessionalForMembershipRemovalService()

    result = service.execute(
        db_session,
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=tenant.id,
            membership_id=membership.id,
        ),
    )
    db_session.delete(membership)
    db_session.flush()

    persisted = db_session.execute(
        select(Professional).where(Professional.id == professional.id)
    ).scalar_one()

    assert result.professional is not None
    assert result.previous_membership_id == membership.id
    assert persisted.membership_id is None
    assert persisted.status is professional_status
    assert persisted.version == 2
    assert db_session.get(Membership, membership.id) is None


def test_unlink_is_tenant_scoped(
    db_session: Session,
) -> None:
    tenant, membership, professional = _create_tenant_membership_and_professional(db_session)
    foreign_tenant = Tenant(name=f"Foreign Clinic {uuid4().hex[:8]}")
    db_session.add(foreign_tenant)
    db_session.flush()
    service = UnlinkProfessionalForMembershipRemovalService()

    result = service.execute(
        db_session,
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=foreign_tenant.id,
            membership_id=membership.id,
        ),
    )
    db_session.refresh(professional)

    assert tenant.id != foreign_tenant.id
    assert result.professional is None
    assert result.previous_membership_id is None
    assert professional.membership_id == membership.id
    assert professional.version == 1


def test_unlink_is_no_op_for_membership_without_professional(
    db_session: Session,
) -> None:
    tenant = Tenant(name=f"Unlinked Membership {uuid4().hex[:8]}")
    user = User(email=f"unlinked-membership-{uuid4().hex}@example.com")
    membership = Membership(
        tenant=tenant,
        user=user,
        role=TenantRole.STAFF,
    )
    db_session.add_all([tenant, membership])
    db_session.flush()
    service = UnlinkProfessionalForMembershipRemovalService()

    result = service.execute(
        db_session,
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=tenant.id,
            membership_id=membership.id,
        ),
    )

    assert result.professional is None
    assert result.previous_membership_id is None
    assert db_session.get(Membership, membership.id) is membership
