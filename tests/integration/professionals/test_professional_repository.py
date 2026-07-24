from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.professionals.contracts import ProfessionalCursor
from clinicops.professionals.enums import (
    ProfessionalListStatus,
    ProfessionalMutableField,
    ProfessionalStatus,
)
from clinicops.professionals.exceptions import (
    ProfessionalExternalReferenceConflictError,
    ProfessionalMembershipLinkConflictError,
)
from clinicops.professionals.models import Professional
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.tenancy.models import Membership, Tenant, TenantRole


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated PostgreSQL session rolled back after each test."""

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
) -> Membership:
    user = User(email=f"repository-{uuid4().hex}@example.com")
    membership = Membership(
        tenant=tenant,
        user=user,
        role=TenantRole.STAFF,
    )
    session.add(membership)
    session.flush()
    return membership


def _create_professional(
    session: Session,
    *,
    tenant: Tenant,
    full_name: str,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    membership_id: UUID | None = None,
    specialty: str | None = None,
    registration_number: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    external_reference: str | None = None,
    created_at: datetime | None = None,
) -> Professional:
    professional = Professional(
        tenant_id=tenant.id,
        membership_id=membership_id,
        full_name=full_name,
        specialty=specialty,
        registration_number=registration_number,
        email=email,
        phone=phone,
        external_reference=external_reference,
        status=status,
    )
    if created_at is not None:
        professional.created_at = created_at
        professional.updated_at = created_at

    session.add(professional)
    session.flush()
    session.refresh(professional)
    return professional


def test_repository_add_flush_and_get_are_tenant_scoped(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Northstar Health Clinic")
    foreign_tenant = _create_tenant(db_session, name="Foreign Clinic")
    repository = ProfessionalRepository(db_session)
    professional = Professional(
        tenant_id=tenant.id,
        full_name="Morgan Reed",
    )

    repository.add(professional)
    repository.flush()

    visible = repository.get_by_id_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
    )
    hidden = repository.get_by_id_for_tenant(
        tenant_id=foreign_tenant.id,
        professional_id=professional.id,
    )

    assert visible is not None
    assert visible.id == professional.id
    assert visible.tenant_id == tenant.id
    assert hidden is None


def test_repository_lists_active_professionals_by_default_filter(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Lakeside Dental Clinic")
    _create_professional(
        db_session,
        tenant=tenant,
        full_name="Active Professional",
    )
    _create_professional(
        db_session,
        tenant=tenant,
        full_name="Archived Professional",
        status=ProfessionalStatus.ARCHIVED,
    )
    repository = ProfessionalRepository(db_session)

    page = repository.list_for_tenant(
        tenant_id=tenant.id,
        limit=50,
        status=ProfessionalListStatus.ACTIVE,
    )

    assert [item.full_name for item in page.items] == ["Active Professional"]
    assert page.next_cursor is None


def test_repository_lists_archived_and_all_statuses(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Harbor Medical Center")
    active = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Active Professional",
    )
    archived = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Archived Professional",
        status=ProfessionalStatus.ARCHIVED,
    )
    repository = ProfessionalRepository(db_session)

    archived_page = repository.list_for_tenant(
        tenant_id=tenant.id,
        limit=50,
        status=ProfessionalListStatus.ARCHIVED,
    )
    all_page = repository.list_for_tenant(
        tenant_id=tenant.id,
        limit=50,
        status=ProfessionalListStatus.ALL,
    )

    assert [item.id for item in archived_page.items] == [archived.id]
    assert {item.id for item in all_page.items} == {active.id, archived.id}


def test_repository_uses_descending_keyset_pagination(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Summit Care Clinic")
    base_time = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    oldest = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Oldest",
        created_at=base_time,
    )
    middle = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Middle",
        created_at=base_time + timedelta(minutes=1),
    )
    newest = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Newest",
        created_at=base_time + timedelta(minutes=2),
    )
    repository = ProfessionalRepository(db_session)

    first_page = repository.list_for_tenant(
        tenant_id=tenant.id,
        limit=2,
        status=ProfessionalListStatus.ACTIVE,
    )

    assert [item.id for item in first_page.items] == [
        newest.id,
        middle.id,
    ]
    assert first_page.next_cursor == ProfessionalCursor(
        created_at=middle.created_at,
        professional_id=middle.id,
    )

    second_page = repository.list_for_tenant(
        tenant_id=tenant.id,
        limit=2,
        status=ProfessionalListStatus.ACTIVE,
        cursor=first_page.next_cursor,
    )

    assert [item.id for item in second_page.items] == [oldest.id]
    assert second_page.next_cursor is None


@pytest.mark.parametrize(
    ("field_name", "field_value", "search"),
    [
        ("full_name", "Morgan Reed", "morgan"),
        ("specialty", "Orthodontics", "ortho"),
        ("registration_number", "DDS-48291", "48291"),
        ("email", "morgan@example.com", "EXAMPLE"),
        ("phone", "+1-202-555-0130", "555"),
        ("external_reference", "PROVIDER-100", "provider"),
    ],
)
def test_repository_searches_supported_profile_fields(
    db_session: Session,
    field_name: str,
    field_value: str,
    search: str,
) -> None:
    tenant = _create_tenant(db_session, name="Cedar Health Clinic")
    values: dict[str, object] = {
        "tenant_id": tenant.id,
        "full_name": "Search Target",
        field_name: field_value,
    }
    professional = Professional(**values)
    db_session.add(professional)
    db_session.flush()
    repository = ProfessionalRepository(db_session)

    page = repository.list_for_tenant(
        tenant_id=tenant.id,
        limit=50,
        status=ProfessionalListStatus.ACTIVE,
        search=search,
    )

    assert [item.id for item in page.items] == [professional.id]


@pytest.mark.parametrize(
    ("stored_value", "search"),
    [
        ("PROVIDER%100", "%"),
        ("PROVIDER_200", "_"),
    ],
)
def test_repository_treats_sql_wildcards_as_literal_search_text(
    db_session: Session,
    stored_value: str,
    search: str,
) -> None:
    tenant = _create_tenant(db_session, name="Maple Health Clinic")
    matching = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Matching",
        external_reference=stored_value,
    )
    _create_professional(
        db_session,
        tenant=tenant,
        full_name="Nonmatching",
        external_reference="PROVIDERX300",
    )
    repository = ProfessionalRepository(db_session)

    page = repository.list_for_tenant(
        tenant_id=tenant.id,
        limit=50,
        status=ProfessionalListStatus.ACTIVE,
        search=search,
    )

    assert [item.id for item in page.items] == [matching.id]


def test_repository_translates_external_reference_conflict(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Evergreen Clinic")
    repository = ProfessionalRepository(db_session)
    repository.add(
        Professional(
            tenant_id=tenant.id,
            full_name="First Professional",
            external_reference="PROVIDER-400",
        )
    )
    repository.flush()
    repository.add(
        Professional(
            tenant_id=tenant.id,
            full_name="Second Professional",
            external_reference="PROVIDER-400",
        )
    )

    with pytest.raises(ProfessionalExternalReferenceConflictError):
        repository.flush()


def test_repository_applies_versioned_active_update_atomically(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Riverside Clinic")
    professional = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Original Name",
    )
    repository = ProfessionalRepository(db_session)

    updated = repository.update_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
        expected_version=1,
        values={
            ProfessionalMutableField.FULL_NAME: "Updated Name",
            ProfessionalMutableField.SPECIALTY: "Dentistry",
        },
    )
    stale = repository.update_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
        expected_version=1,
        values={
            ProfessionalMutableField.FULL_NAME: "Stale Name",
        },
    )

    assert updated is not None
    assert updated.full_name == "Updated Name"
    assert updated.specialty == "Dentistry"
    assert updated.version == 2
    assert stale is None


def test_repository_archives_and_restores_with_status_and_version_guards(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Oak Valley Clinic")
    professional = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Lifecycle Professional",
    )
    repository = ProfessionalRepository(db_session)

    archived = repository.archive_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
        expected_version=1,
    )
    repeated_archive = repository.archive_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
        expected_version=2,
    )
    restored = repository.restore_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
        expected_version=2,
    )

    assert archived is not None
    assert archived.status is ProfessionalStatus.ARCHIVED
    assert archived.version == 2
    assert repeated_archive is None
    assert restored is not None
    assert restored.status is ProfessionalStatus.ACTIVE
    assert restored.version == 3


def test_repository_links_and_unlinks_membership_atomically(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Pine Health Clinic")
    membership = _create_membership(db_session, tenant=tenant)
    professional = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Linked Professional",
    )
    repository = ProfessionalRepository(db_session)

    linked = repository.link_membership_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
        membership_id=membership.id,
        expected_version=1,
    )
    repeated_link = repository.link_membership_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
        membership_id=membership.id,
        expected_version=2,
    )
    unlinked = repository.unlink_membership_for_tenant(
        tenant_id=tenant.id,
        professional_id=professional.id,
        expected_version=2,
    )

    assert linked is not None
    assert linked.membership_id == membership.id
    assert linked.version == 2
    assert repeated_link is None
    assert unlinked is not None
    assert unlinked.membership_id is None
    assert unlinked.version == 3


def test_repository_translates_membership_link_conflict(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Willow Clinic")
    membership = _create_membership(db_session, tenant=tenant)
    first = _create_professional(
        db_session,
        tenant=tenant,
        full_name="First Professional",
        membership_id=membership.id,
    )
    second = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Second Professional",
    )
    repository = ProfessionalRepository(db_session)

    assert first.membership_id == membership.id

    with pytest.raises(ProfessionalMembershipLinkConflictError):
        repository.link_membership_for_tenant(
            tenant_id=tenant.id,
            professional_id=second.id,
            membership_id=membership.id,
            expected_version=1,
        )


def test_repository_unlinks_by_membership_for_membership_removal(
    db_session: Session,
) -> None:
    tenant = _create_tenant(db_session, name="Aspen Medical Center")
    membership = _create_membership(db_session, tenant=tenant)
    professional = _create_professional(
        db_session,
        tenant=tenant,
        full_name="Removal Professional",
        membership_id=membership.id,
    )
    repository = ProfessionalRepository(db_session)

    linked = repository.get_linked_by_membership_for_tenant(
        tenant_id=tenant.id,
        membership_id=membership.id,
    )
    unlinked = repository.unlink_by_membership_for_tenant(
        tenant_id=tenant.id,
        membership_id=membership.id,
    )
    repeated_unlink = repository.unlink_by_membership_for_tenant(
        tenant_id=tenant.id,
        membership_id=membership.id,
    )

    assert linked is not None
    assert linked.id == professional.id
    assert unlinked is not None
    assert unlinked.membership_id is None
    assert unlinked.version == 2
    assert repeated_unlink is None
