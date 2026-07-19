from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.tenancy.exceptions import (
    InvalidOwnershipTransferError,
    MembershipDisabledError,
    MembershipNotFoundError,
    TenantDisabledError,
    TenantNotFoundError,
    TenantOwnershipConflictError,
)
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.services.transfer_ownership import (
    TransferTenantOwnershipCommand,
    TransferTenantOwnershipService,
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
    """Create a uniquely addressable global user."""

    return User(email=f"{prefix}-{uuid4()}@example.com")


def create_transfer_fixture(
    db_session: Session,
    *,
    target_role: TenantRole = TenantRole.STAFF,
    target_status: MembershipStatus = MembershipStatus.ACTIVE,
    tenant_status: TenantStatus = TenantStatus.ACTIVE,
) -> tuple[Tenant, Membership, Membership]:
    """Persist a tenant with an owner and one transfer target."""

    owner_user = create_user("current-owner")
    target_user = create_user("target-owner")
    tenant = Tenant(
        name=f"Ownership Clinic {uuid4()}",
        status=tenant_status,
        disabled_at=(datetime.now(UTC) if tenant_status is TenantStatus.DISABLED else None),
    )
    current_owner = Membership(
        tenant=tenant,
        user=owner_user,
        role=TenantRole.OWNER,
    )
    target_membership = Membership(
        tenant=tenant,
        user=target_user,
        role=target_role,
        status=target_status,
        disabled_at=(datetime.now(UTC) if target_status is MembershipStatus.DISABLED else None),
    )

    db_session.add_all(
        [
            tenant,
            owner_user,
            target_user,
            current_owner,
            target_membership,
        ]
    )
    db_session.flush()

    return tenant, current_owner, target_membership


@pytest.mark.parametrize(
    "target_role",
    [TenantRole.ADMIN, TenantRole.STAFF],
)
def test_transfer_ownership_demotes_owner_and_promotes_target(
    db_session: Session,
    target_role: TenantRole,
) -> None:
    tenant, current_owner, target_membership = create_transfer_fixture(
        db_session,
        target_role=target_role,
    )
    service = TransferTenantOwnershipService()

    result = service.execute(
        db_session,
        TransferTenantOwnershipCommand(
            tenant_id=tenant.id,
            expected_current_owner_user_id=current_owner.user_id,
            new_owner_user_id=target_membership.user_id,
        ),
    )

    db_session.expire_all()

    stored_memberships = list(
        db_session.scalars(select(Membership).where(Membership.tenant_id == tenant.id))
    )
    role_by_user_id = {membership.user_id: membership.role for membership in stored_memberships}

    assert result.tenant_id == tenant.id
    assert result.previous_owner_user_id == current_owner.user_id
    assert result.new_owner_user_id == target_membership.user_id
    assert role_by_user_id[current_owner.user_id] is TenantRole.ADMIN
    assert role_by_user_id[target_membership.user_id] is TenantRole.OWNER
    assert (
        sum(
            membership.role is TenantRole.OWNER and membership.status is MembershipStatus.ACTIVE
            for membership in stored_memberships
        )
        == 1
    )


def test_transfer_rejects_current_owner_as_target(
    db_session: Session,
) -> None:
    tenant, current_owner, _ = create_transfer_fixture(db_session)
    service = TransferTenantOwnershipService()

    with pytest.raises(InvalidOwnershipTransferError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=current_owner.user_id,
                new_owner_user_id=current_owner.user_id,
            ),
        )

    assert current_owner.role is TenantRole.OWNER


def test_transfer_rejects_missing_target_membership(
    db_session: Session,
) -> None:
    tenant, current_owner, _ = create_transfer_fixture(db_session)
    service = TransferTenantOwnershipService()

    with pytest.raises(MembershipNotFoundError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=current_owner.user_id,
                new_owner_user_id=uuid4(),
            ),
        )

    assert current_owner.role is TenantRole.OWNER


def test_transfer_rejects_disabled_target_membership(
    db_session: Session,
) -> None:
    tenant, current_owner, target_membership = create_transfer_fixture(
        db_session,
        target_status=MembershipStatus.DISABLED,
    )
    service = TransferTenantOwnershipService()

    with pytest.raises(MembershipDisabledError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=current_owner.user_id,
                new_owner_user_id=target_membership.user_id,
            ),
        )

    assert current_owner.role is TenantRole.OWNER
    assert target_membership.role is TenantRole.STAFF


def test_transfer_rejects_membership_from_another_tenant(
    db_session: Session,
) -> None:
    tenant, current_owner, _ = create_transfer_fixture(db_session)
    external_user = create_user("external-member")
    other_tenant = Tenant(name=f"Other Clinic {uuid4()}")
    external_membership = Membership(
        tenant=other_tenant,
        user=external_user,
        role=TenantRole.ADMIN,
    )

    db_session.add_all(
        [
            external_user,
            other_tenant,
            external_membership,
        ]
    )
    db_session.flush()

    service = TransferTenantOwnershipService()

    with pytest.raises(MembershipNotFoundError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=current_owner.user_id,
                new_owner_user_id=external_user.id,
            ),
        )

    assert current_owner.role is TenantRole.OWNER
    assert external_membership.role is TenantRole.ADMIN


def test_transfer_rejects_stale_expected_owner(
    db_session: Session,
) -> None:
    tenant, current_owner, target_membership = create_transfer_fixture(db_session)
    service = TransferTenantOwnershipService()

    with pytest.raises(TenantOwnershipConflictError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=target_membership.user_id,
                new_owner_user_id=target_membership.user_id,
            ),
        )

    assert current_owner.role is TenantRole.OWNER
    assert target_membership.role is TenantRole.STAFF


def test_transfer_rejects_tenant_without_active_owner(
    db_session: Session,
) -> None:
    user = create_user("ownerless-member")
    tenant = Tenant(name=f"Ownerless Clinic {uuid4()}")
    membership = Membership(
        tenant=tenant,
        user=user,
        role=TenantRole.ADMIN,
    )

    db_session.add_all([user, tenant, membership])
    db_session.flush()

    service = TransferTenantOwnershipService()

    with pytest.raises(TenantOwnershipConflictError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=uuid4(),
                new_owner_user_id=user.id,
            ),
        )


def test_transfer_rejects_disabled_tenant(
    db_session: Session,
) -> None:
    tenant, current_owner, target_membership = create_transfer_fixture(
        db_session,
        tenant_status=TenantStatus.DISABLED,
    )
    service = TransferTenantOwnershipService()

    with pytest.raises(TenantDisabledError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=current_owner.user_id,
                new_owner_user_id=target_membership.user_id,
            ),
        )

    assert current_owner.role is TenantRole.OWNER
    assert target_membership.role is TenantRole.STAFF


def test_transfer_rejects_missing_tenant(
    db_session: Session,
) -> None:
    service = TransferTenantOwnershipService()

    with pytest.raises(TenantNotFoundError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=uuid4(),
                expected_current_owner_user_id=uuid4(),
                new_owner_user_id=uuid4(),
            ),
        )
