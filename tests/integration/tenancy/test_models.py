from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def create_user(prefix: str) -> User:
    """Create a uniquely addressable global user for persistence tests."""

    return User(email=f"{prefix}-{uuid4()}@example.com")


def assert_unique_constraint(
    exception_info: pytest.ExceptionInfo[IntegrityError],
    constraint_name: str,
) -> None:
    """Assert that PostgreSQL rejected a named unique constraint."""

    original_exception = exception_info.value.orig

    assert isinstance(original_exception, UniqueViolation)
    assert original_exception.diag.constraint_name == constraint_name


def test_tenant_and_memberships_persist_with_lifecycle_defaults(
    db_session: Session,
) -> None:
    owner_user = create_user("owner")
    staff_user = create_user("staff")
    tenant = Tenant(name="São Lucas Clinic")
    owner_membership = Membership(
        tenant=tenant,
        user=owner_user,
        role=TenantRole.OWNER,
    )
    staff_membership = Membership(
        tenant=tenant,
        user=staff_user,
        role=TenantRole.STAFF,
    )

    db_session.add_all(
        [
            tenant,
            owner_user,
            staff_user,
            owner_membership,
            staff_membership,
        ]
    )
    db_session.flush()

    tenant_id = tenant.id
    db_session.expire_all()

    stored_tenant = db_session.scalar(select(Tenant).where(Tenant.id == tenant_id))

    assert stored_tenant is not None
    assert stored_tenant.name == "São Lucas Clinic"
    assert stored_tenant.status is TenantStatus.ACTIVE
    assert stored_tenant.disabled_at is None
    assert stored_tenant.created_at.tzinfo is not None
    assert stored_tenant.updated_at.tzinfo is not None

    stored_memberships = stored_tenant.memberships

    assert len(stored_memberships) == 2
    assert {membership.role for membership in stored_memberships} == {
        TenantRole.OWNER,
        TenantRole.STAFF,
    }
    assert all(membership.status is MembershipStatus.ACTIVE for membership in stored_memberships)
    assert all(membership.disabled_at is None for membership in stored_memberships)
    assert all(membership.created_at.tzinfo is not None for membership in stored_memberships)
    assert all(membership.updated_at.tzinfo is not None for membership in stored_memberships)


def test_membership_is_unique_per_user_and_tenant(
    db_session: Session,
) -> None:
    user = create_user("member")
    tenant = Tenant(name="Unique Membership Clinic")
    first_membership = Membership(
        tenant=tenant,
        user=user,
        role=TenantRole.STAFF,
    )

    db_session.add_all([tenant, user, first_membership])
    db_session.flush()

    db_session.add(
        Membership(
            tenant=tenant,
            user=user,
            role=TenantRole.ADMIN,
        )
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_unique_constraint(
        exception_info,
        "uq_memberships_user_id_tenant_id",
    )


def test_tenant_allows_at_most_one_active_owner(
    db_session: Session,
) -> None:
    first_user = create_user("first-owner")
    second_user = create_user("second-owner")
    tenant = Tenant(name="Owner Constraint Clinic")

    db_session.add_all(
        [
            tenant,
            first_user,
            second_user,
            Membership(
                tenant=tenant,
                user=first_user,
                role=TenantRole.OWNER,
            ),
            Membership(
                tenant=tenant,
                user=second_user,
                role=TenantRole.OWNER,
            ),
        ]
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_unique_constraint(
        exception_info,
        "uq_memberships_one_active_owner_per_tenant",
    )


def test_disabled_owner_does_not_conflict_with_active_owner(
    db_session: Session,
) -> None:
    active_owner_user = create_user("active-owner")
    disabled_owner_user = create_user("disabled-owner")
    tenant = Tenant(name="Historical Ownership Clinic")

    db_session.add_all(
        [
            tenant,
            active_owner_user,
            disabled_owner_user,
            Membership(
                tenant=tenant,
                user=active_owner_user,
                role=TenantRole.OWNER,
            ),
            Membership(
                tenant=tenant,
                user=disabled_owner_user,
                role=TenantRole.OWNER,
                status=MembershipStatus.DISABLED,
                disabled_at=datetime.now(UTC),
            ),
        ]
    )
    db_session.flush()

    assert len(tenant.memberships) == 2
    assert (
        sum(
            membership.role is TenantRole.OWNER and membership.status is MembershipStatus.ACTIVE
            for membership in tenant.memberships
        )
        == 1
    )
