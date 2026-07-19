from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.exceptions import (
    UserDisabledError,
    UserNotFoundError,
)
from clinicops.identity.models import User, UserStatus
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.repository import TenantRepository
from clinicops.tenancy.services.create_tenant import (
    CreateTenantCommand,
    CreateTenantService,
)


class SimulatedTenantCreationError(RuntimeError):
    """Represent a failure after the tenant aggregate is flushed."""


class FailingTenantRepository(TenantRepository):
    """Flush the aggregate and then simulate a later workflow failure."""

    def add_and_flush(
        self,
        session: Session,
        tenant: Tenant,
    ) -> None:
        super().add_and_flush(session, tenant)
        raise SimulatedTenantCreationError


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def create_user(
    prefix: str,
    *,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    """Create a uniquely addressable global user."""

    return User(
        email=f"{prefix}-{uuid4()}@example.com",
        status=status,
        disabled_at=(datetime.now(UTC) if status is UserStatus.DISABLED else None),
    )


def persist_user(user: User) -> UUID:
    """Persist a user for tests that cross transaction boundaries."""

    with Session(get_engine()) as session:
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()

    return user_id


def delete_user(user_id: UUID) -> None:
    """Remove a committed test user after its dependent data is gone."""

    with Session(get_engine()) as session:
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


def test_create_tenant_persists_normalized_name_and_initial_owner(
    db_session: Session,
) -> None:
    owner_user = create_user("tenant-owner")
    db_session.add(owner_user)
    db_session.flush()

    service = CreateTenantService()
    created_tenant = service.execute(
        db_session,
        CreateTenantCommand(
            name="  Northstar Health Clinic  ",
            owner_user_id=owner_user.id,
        ),
    )

    stored_tenant = db_session.scalar(select(Tenant).where(Tenant.id == created_tenant.id))

    assert stored_tenant is not None
    assert created_tenant.name == "Northstar Health Clinic"
    assert created_tenant.status is TenantStatus.ACTIVE
    assert created_tenant.owner_user_id == owner_user.id
    assert created_tenant.created_at.tzinfo is not None
    assert stored_tenant.disabled_at is None

    assert len(stored_tenant.memberships) == 1

    owner_membership = stored_tenant.memberships[0]

    assert owner_membership.user_id == owner_user.id
    assert owner_membership.role is TenantRole.OWNER
    assert owner_membership.status is MembershipStatus.ACTIVE
    assert owner_membership.disabled_at is None


def test_create_tenant_rejects_missing_owner_user(
    db_session: Session,
) -> None:
    tenant_name = f"Missing Owner Clinic {uuid4()}"
    service = CreateTenantService()

    with pytest.raises(UserNotFoundError):
        service.execute(
            db_session,
            CreateTenantCommand(
                name=tenant_name,
                owner_user_id=uuid4(),
            ),
        )

    stored_tenant = db_session.scalar(select(Tenant).where(Tenant.name == tenant_name))

    assert stored_tenant is None


def test_create_tenant_rejects_disabled_owner_user(
    db_session: Session,
) -> None:
    owner_user = create_user(
        "disabled-owner",
        status=UserStatus.DISABLED,
    )
    db_session.add(owner_user)
    db_session.flush()

    tenant_name = f"Disabled Owner Clinic {uuid4()}"
    service = CreateTenantService()

    with pytest.raises(UserDisabledError):
        service.execute(
            db_session,
            CreateTenantCommand(
                name=tenant_name,
                owner_user_id=owner_user.id,
            ),
        )

    stored_tenant = db_session.scalar(select(Tenant).where(Tenant.name == tenant_name))

    assert stored_tenant is None


def test_create_tenant_does_not_commit_the_transaction() -> None:
    owner_user = create_user("rollback-owner")
    owner_user_id = persist_user(owner_user)
    tenant_name = f"Rollback Clinic {uuid4()}"

    try:
        with Session(get_engine()) as session:
            CreateTenantService().execute(
                session,
                CreateTenantCommand(
                    name=tenant_name,
                    owner_user_id=owner_user_id,
                ),
            )
            session.rollback()

        with Session(get_engine()) as verification_session:
            stored_tenant = verification_session.scalar(
                select(Tenant).where(Tenant.name == tenant_name)
            )

        assert stored_tenant is None
    finally:
        delete_user(owner_user_id)


def test_later_failure_rolls_back_tenant_and_owner_membership() -> None:
    owner_user = create_user("atomic-owner")
    owner_user_id = persist_user(owner_user)
    tenant_name = f"Atomic Clinic {uuid4()}"
    service = CreateTenantService(
        tenant_repository=FailingTenantRepository(),
    )

    try:
        with Session(get_engine()) as session:
            with pytest.raises(SimulatedTenantCreationError):
                service.execute(
                    session,
                    CreateTenantCommand(
                        name=tenant_name,
                        owner_user_id=owner_user_id,
                    ),
                )

            session.rollback()

        with Session(get_engine()) as verification_session:
            stored_tenant = verification_session.scalar(
                select(Tenant).where(Tenant.name == tenant_name)
            )
            stored_membership = verification_session.scalar(
                select(Membership).where(Membership.user_id == owner_user_id)
            )

        assert stored_tenant is None
        assert stored_membership is None
    finally:
        delete_user(owner_user_id)
