from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import User, UserStatus
from clinicops.tenancy.exceptions import (
    MembershipActorNotAuthorizedError,
    MembershipNotFoundError,
    MembershipOwnerProtectedError,
    MembershipSelfManagementNotAllowedError,
    TenantDisabledError,
    TenantNotFoundError,
)
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.services.membership_administration import (
    RemoveMembershipCommand,
)
from clinicops.tenancy.services.remove_membership import (
    RemoveMembershipService,
)


@dataclass(frozen=True, slots=True)
class MembershipScenario:
    """Persisted membership-removal state."""

    tenant: Tenant
    actor: User
    actor_membership: Membership
    target: User
    target_membership: Membership


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated PostgreSQL transaction."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def create_user(
    session: Session,
    *,
    prefix: str,
) -> User:
    """Persist one active global user."""

    user = User(
        email=f"{prefix}-{uuid4().hex}@example.com",
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()

    return user


def create_scenario(
    session: Session,
    *,
    actor_role: TenantRole = TenantRole.OWNER,
    actor_status: MembershipStatus = MembershipStatus.ACTIVE,
    target_role: TenantRole = TenantRole.STAFF,
    target_status: MembershipStatus = MembershipStatus.ACTIVE,
    target_is_owner: bool = False,
    tenant_status: TenantStatus = TenantStatus.ACTIVE,
) -> MembershipScenario:
    """Persist one tenant with owner, actor, and target memberships."""

    tenant = Tenant(
        name=f"Membership Removal Clinic {uuid4().hex[:8]}",
        status=tenant_status,
        disabled_at=(datetime.now(UTC) if tenant_status is TenantStatus.DISABLED else None),
    )
    session.add(tenant)

    owner = create_user(
        session,
        prefix="removal-owner",
    )
    owner_membership = Membership(
        tenant=tenant,
        user=owner,
        role=TenantRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    session.add(owner_membership)

    if actor_role is TenantRole.OWNER:
        actor = owner
        actor_membership = owner_membership
    else:
        actor = create_user(
            session,
            prefix="removal-actor",
        )
        actor_membership = Membership(
            tenant=tenant,
            user=actor,
            role=actor_role,
            status=actor_status,
            disabled_at=(datetime.now(UTC) if actor_status is MembershipStatus.DISABLED else None),
        )
        session.add(actor_membership)

    if target_is_owner:
        target = owner
        target_membership = owner_membership
    else:
        target = create_user(
            session,
            prefix="removal-target",
        )
        target_membership = Membership(
            tenant=tenant,
            user=target,
            role=target_role,
            status=target_status,
            disabled_at=(datetime.now(UTC) if target_status is MembershipStatus.DISABLED else None),
        )
        session.add(target_membership)

    session.flush()

    return MembershipScenario(
        tenant=tenant,
        actor=actor,
        actor_membership=actor_membership,
        target=target,
        target_membership=target_membership,
    )


@pytest.mark.parametrize(
    "target_status",
    [
        MembershipStatus.ACTIVE,
        MembershipStatus.DISABLED,
    ],
)
def test_remove_membership_deletes_only_tenant_relationship(
    db_session: Session,
    target_status: MembershipStatus,
) -> None:
    scenario = create_scenario(
        db_session,
        target_status=target_status,
    )
    target_user_id = scenario.target.id
    target_membership_id = scenario.target_membership.id
    service = RemoveMembershipService()

    result = service.execute(
        db_session,
        RemoveMembershipCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=target_membership_id,
        ),
    )

    assert result.membership_id == target_membership_id
    assert result.tenant_id == scenario.tenant.id
    assert result.user_id == target_user_id
    assert db_session.get(Membership, target_membership_id) is None
    assert db_session.get(User, target_user_id) is not None


def test_remove_membership_preserves_other_tenant_membership(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    second_tenant = Tenant(
        name=f"Second Membership Clinic {uuid4().hex[:8]}",
        status=TenantStatus.ACTIVE,
    )
    second_owner = create_user(
        db_session,
        prefix="second-owner",
    )
    second_owner_membership = Membership(
        tenant=second_tenant,
        user=second_owner,
        role=TenantRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    second_membership = Membership(
        tenant=second_tenant,
        user=scenario.target,
        role=TenantRole.STAFF,
        status=MembershipStatus.ACTIVE,
    )
    db_session.add_all(
        [
            second_tenant,
            second_owner_membership,
            second_membership,
        ]
    )
    db_session.flush()
    second_membership_id = second_membership.id

    RemoveMembershipService().execute(
        db_session,
        RemoveMembershipCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=scenario.target_membership.id,
        ),
    )

    assert (
        db_session.get(
            Membership,
            scenario.target_membership.id,
        )
        is None
    )
    preserved_membership = db_session.get(
        Membership,
        second_membership_id,
    )
    assert preserved_membership is not None
    assert preserved_membership.tenant_id == second_tenant.id
    assert preserved_membership.user_id == scenario.target.id


def test_remove_membership_protects_owner(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
        target_is_owner=True,
    )
    service = RemoveMembershipService()

    with pytest.raises(MembershipOwnerProtectedError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    assert (
        db_session.get(
            Membership,
            scenario.target_membership.id,
        )
        is not None
    )


def test_remove_membership_rejects_self_management(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
    )
    service = RemoveMembershipService()

    with pytest.raises(MembershipSelfManagementNotAllowedError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.actor_membership.id,
            ),
        )

    assert (
        db_session.get(
            Membership,
            scenario.actor_membership.id,
        )
        is not None
    )


@pytest.mark.parametrize(
    ("actor_role", "actor_status"),
    [
        (
            TenantRole.STAFF,
            MembershipStatus.ACTIVE,
        ),
        (
            TenantRole.ADMIN,
            MembershipStatus.DISABLED,
        ),
    ],
)
def test_remove_membership_revalidates_actor_authorization(
    db_session: Session,
    actor_role: TenantRole,
    actor_status: MembershipStatus,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=actor_role,
        actor_status=actor_status,
    )
    service = RemoveMembershipService()

    with pytest.raises(MembershipActorNotAuthorizedError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    assert (
        db_session.get(
            Membership,
            scenario.target_membership.id,
        )
        is not None
    )


def test_remove_membership_rejects_cross_tenant_target(
    db_session: Session,
) -> None:
    source = create_scenario(db_session)
    foreign = create_scenario(db_session)
    service = RemoveMembershipService()

    with pytest.raises(MembershipNotFoundError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=source.tenant.id,
                actor_user_id=source.actor.id,
                membership_id=foreign.target_membership.id,
            ),
        )

    assert (
        db_session.get(
            Membership,
            foreign.target_membership.id,
        )
        is not None
    )


def test_remove_membership_rejects_disabled_tenant(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        tenant_status=TenantStatus.DISABLED,
    )
    service = RemoveMembershipService()

    with pytest.raises(TenantDisabledError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    assert (
        db_session.get(
            Membership,
            scenario.target_membership.id,
        )
        is not None
    )


def test_remove_membership_rejects_missing_tenant(
    db_session: Session,
) -> None:
    service = RemoveMembershipService()

    with pytest.raises(TenantNotFoundError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=uuid4(),
                actor_user_id=uuid4(),
                membership_id=uuid4(),
            ),
        )


def test_successful_removal_does_not_commit(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    service = RemoveMembershipService()

    with patch.object(
        db_session,
        "commit",
        wraps=db_session.commit,
    ) as commit:
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    commit.assert_not_called()
