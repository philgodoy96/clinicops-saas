from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.models import AuditLogEntry
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


def _audit_context(*, user_id: UUID) -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=user_id,
        role=TenantRole.ADMIN.value,
        request_id="request-membership-removal-unlink",
        correlation_id="correlation-membership-removal-unlink",
    )


def _create_tenant_membership_and_professional(
    session: Session,
    *,
    professional_status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
) -> tuple[Tenant, Membership, Professional, User]:
    tenant = Tenant(name=f"Professional Unlink {uuid4().hex[:8]}")
    user = User(email=f"professional-unlink-{uuid4().hex}@example.com")
    actor = User(email=f"professional-unlink-actor-{uuid4().hex}@example.com")
    membership = Membership(
        tenant=tenant,
        user=user,
        role=TenantRole.STAFF,
    )

    session.add_all([tenant, membership, actor])
    session.flush()

    professional = Professional(
        tenant_id=tenant.id,
        membership_id=membership.id,
        full_name="Morgan Reed",
        specialty="Dentistry",
        registration_number="DDS-48291",
        registration_region="CA",
        email="morgan@example.com",
        phone="+1-202-555-0130",
        external_reference="PROVIDER-100",
        status=professional_status,
    )
    session.add(professional)
    session.flush()
    session.refresh(professional)

    return tenant, membership, professional, actor


def _professional_audit_entries(
    session: Session,
    *,
    tenant_id: UUID,
) -> tuple[AuditLogEntry, ...]:
    return tuple(
        session.execute(
            select(AuditLogEntry).where(
                AuditLogEntry.tenant_id == tenant_id,
                AuditLogEntry.resource_type == AuditResourceType.PROFESSIONAL.value,
            )
        ).scalars()
    )


def _metadata_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        strings: set[str] = set()
        for item in value:
            strings.update(_metadata_strings(item))
        return strings
    if isinstance(value, dict):
        strings = set()
        for key, item in value.items():
            strings.add(str(key))
            strings.update(_metadata_strings(item))
        return strings
    return set()


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
    tenant, membership, professional, actor = _create_tenant_membership_and_professional(
        db_session,
        professional_status=professional_status,
    )
    removed_membership_id = membership.id
    audit_context = _audit_context(user_id=actor.id)
    service = UnlinkProfessionalForMembershipRemovalService()

    result = service.execute(
        db_session,
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=tenant.id,
            membership_id=membership.id,
            audit_context=audit_context,
        ),
    )

    persisted = db_session.execute(
        select(Professional).where(Professional.id == professional.id)
    ).scalar_one()
    audit_entries = _professional_audit_entries(
        db_session,
        tenant_id=tenant.id,
    )

    db_session.delete(membership)
    db_session.flush()

    assert db_session.in_transaction() is True
    assert result.professional is not None
    assert result.previous_membership_id == removed_membership_id
    assert persisted.membership_id is None
    assert persisted.status is professional_status
    assert persisted.version == 2
    assert db_session.get(Membership, removed_membership_id) is None

    assert len(audit_entries) == 1
    audit_entry = audit_entries[0]
    assert audit_entry.action == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
    assert audit_entry.action == "professional.membership_unlinked"
    assert audit_entry.resource_type == AuditResourceType.PROFESSIONAL.value
    assert audit_entry.resource_type == "professional"
    assert audit_entry.resource_id == str(persisted.id)
    assert audit_entry.event_metadata == {
        "membership_id": str(removed_membership_id),
        "reason": "membership_removal",
        "version": persisted.version,
    }
    assert audit_entry.actor_user_id == audit_context.actor.user_id
    assert audit_entry.actor_role == audit_context.actor.role
    assert audit_entry.source == audit_context.source.value
    assert audit_entry.request_id == audit_context.request_id
    assert audit_entry.correlation_id == audit_context.correlation_id

    forbidden_values = {
        "Morgan Reed",
        "Dentistry",
        "DDS-48291",
        "CA",
        "morgan@example.com",
        "+1-202-555-0130",
        "PROVIDER-100",
    }
    assert forbidden_values.isdisjoint(_metadata_strings(audit_entry.event_metadata))


def test_unlink_is_tenant_scoped(
    db_session: Session,
) -> None:
    tenant, membership, professional, actor = _create_tenant_membership_and_professional(db_session)
    foreign_tenant = Tenant(name=f"Foreign Clinic {uuid4().hex[:8]}")
    db_session.add(foreign_tenant)
    db_session.flush()
    audit_context = _audit_context(user_id=actor.id)
    service = UnlinkProfessionalForMembershipRemovalService()

    result = service.execute(
        db_session,
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=foreign_tenant.id,
            membership_id=membership.id,
            audit_context=audit_context,
        ),
    )
    db_session.refresh(professional)

    assert tenant.id != foreign_tenant.id
    assert result.professional is None
    assert result.previous_membership_id is None
    assert professional.membership_id == membership.id
    assert professional.version == 1
    assert _professional_audit_entries(db_session, tenant_id=tenant.id) == ()
    assert _professional_audit_entries(db_session, tenant_id=foreign_tenant.id) == ()


def test_unlink_is_no_op_for_membership_without_professional(
    db_session: Session,
) -> None:
    tenant = Tenant(name=f"Unlinked Membership {uuid4().hex[:8]}")
    user = User(email=f"unlinked-membership-{uuid4().hex}@example.com")
    actor = User(email=f"unlinked-membership-actor-{uuid4().hex}@example.com")
    membership = Membership(
        tenant=tenant,
        user=user,
        role=TenantRole.STAFF,
    )
    db_session.add_all([tenant, membership, actor])
    db_session.flush()
    audit_context = _audit_context(user_id=actor.id)
    service = UnlinkProfessionalForMembershipRemovalService()

    result = service.execute(
        db_session,
        UnlinkProfessionalForMembershipRemovalCommand(
            tenant_id=tenant.id,
            membership_id=membership.id,
            audit_context=audit_context,
        ),
    )

    assert result.professional is None
    assert result.previous_membership_id is None
    assert db_session.get(Membership, membership.id) is membership
    assert _professional_audit_entries(db_session, tenant_id=tenant.id) == ()
