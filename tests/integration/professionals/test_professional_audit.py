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
    ArchiveProfessionalCommand,
    CreateProfessionalCommand,
    RestoreProfessionalCommand,
    UpdateProfessionalCommand,
)
from clinicops.professionals.enums import ProfessionalMutableField
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.archive_professional import (
    ArchiveProfessionalService,
)
from clinicops.professionals.services.create_professional import (
    CreateProfessionalService,
)
from clinicops.professionals.services.restore_professional import (
    RestoreProfessionalService,
)
from clinicops.professionals.services.update_professional import (
    UpdateProfessionalService,
)
from clinicops.tenancy.models import Tenant, TenantRole


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
        request_id="request-professional-lifecycle",
        correlation_id="correlation-professional-lifecycle",
    )


def test_professional_lifecycle_records_safe_audit_facts_in_one_transaction(
    db_session: Session,
) -> None:
    tenant = Tenant(name=f"Professional Audit {uuid4().hex[:8]}")
    actor = User(email=f"professional-audit-{uuid4().hex}@example.com")
    db_session.add_all([tenant, actor])
    db_session.flush()

    repository = ProfessionalRepository(db_session)
    audit_context = _audit_context(user_id=actor.id)

    created = (
        CreateProfessionalService(
            repository,
            db_session,
        )
        .execute(
            CreateProfessionalCommand(
                tenant_id=tenant.id,
                full_name="Morgan Reed",
                audit_context=audit_context,
                specialty="Dentistry",
                registration_number="DDS-48291",
                registration_region="CA",
                email="morgan@example.com",
                phone="+1-202-555-0130",
                external_reference="PROVIDER-100",
            )
        )
        .professional
    )

    updated = (
        UpdateProfessionalService(
            repository,
            db_session,
        )
        .execute(
            UpdateProfessionalCommand(
                tenant_id=tenant.id,
                professional_id=created.id,
                expected_version=created.version,
                fields_to_update=frozenset(
                    {
                        ProfessionalMutableField.SPECIALTY,
                        ProfessionalMutableField.EMAIL,
                    }
                ),
                specialty="Orthodontics",
                email="updated@example.com",
                audit_context=audit_context,
            )
        )
        .professional
    )

    archived = (
        ArchiveProfessionalService(
            repository,
            db_session,
        )
        .execute(
            ArchiveProfessionalCommand(
                tenant_id=tenant.id,
                professional_id=created.id,
                expected_version=updated.version,
                audit_context=audit_context,
            )
        )
        .professional
    )

    restored = (
        RestoreProfessionalService(
            repository,
            db_session,
        )
        .execute(
            RestoreProfessionalCommand(
                tenant_id=tenant.id,
                professional_id=created.id,
                expected_version=archived.version,
                audit_context=audit_context,
            )
        )
        .professional
    )

    entries = tuple(
        db_session.execute(
            select(AuditLogEntry).where(
                AuditLogEntry.tenant_id == tenant.id,
                AuditLogEntry.resource_type == AuditResourceType.PROFESSIONAL.value,
                AuditLogEntry.resource_id == str(created.id),
            )
        ).scalars()
    )
    entries_by_action = {entry.action: entry for entry in entries}

    assert set(entries_by_action) == {
        AuditAction.PROFESSIONAL_CREATED.value,
        AuditAction.PROFESSIONAL_UPDATED.value,
        AuditAction.PROFESSIONAL_ARCHIVED.value,
        AuditAction.PROFESSIONAL_RESTORED.value,
    }
    assert entries_by_action[AuditAction.PROFESSIONAL_CREATED.value].event_metadata == {
        "status": "active",
        "version": 1,
    }
    assert entries_by_action[AuditAction.PROFESSIONAL_UPDATED.value].event_metadata == {
        "version": 2,
        "changed_fields": ["specialty", "email"],
    }
    assert entries_by_action[AuditAction.PROFESSIONAL_ARCHIVED.value].event_metadata == {
        "previous_status": "active",
        "new_status": "archived",
        "version": 3,
    }
    assert entries_by_action[AuditAction.PROFESSIONAL_RESTORED.value].event_metadata == {
        "previous_status": "archived",
        "new_status": "active",
        "version": 4,
    }

    forbidden_values = {
        "Morgan Reed",
        "Dentistry",
        "Orthodontics",
        "DDS-48291",
        "CA",
        "morgan@example.com",
        "updated@example.com",
        "+1-202-555-0130",
        "PROVIDER-100",
    }
    assert all(
        forbidden_values.isdisjoint(_metadata_strings(entry.event_metadata)) for entry in entries
    )
    assert all(
        entry.actor_user_id == audit_context.actor.user_id
        and entry.actor_role == audit_context.actor.role
        and entry.request_id == audit_context.request_id
        and entry.correlation_id == audit_context.correlation_id
        for entry in entries
    )
    assert restored.version == 4


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
