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
    MembershipDisabledError,
    MembershipNotFoundError,
    MembershipOwnerProtectedError,
    MembershipRoleNotAllowedError,
    MembershipSelfManagementNotAllowedError,
    TenantDisabledError,
    TenantNotFoundError,
)
from clinicops.tenancy.membership_administration_repository import (
    MembershipAdministrationRepository,
)
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.services.change_membership_role import (
    ChangeMembershipRoleService,
)
from clinicops.tenancy.services.membership_administration import (
    ChangeMembershipRoleCommand,
)


@dataclass(frozen=True, slots=True)
class MembershipScenario:
    """Persisted role-management state."""

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
    """Persist one tenant with actor, owner, and target memberships."""

    tenant = Tenant(
        name=f"Role Management Clinic {uuid4().hex[:8]}",
        status=tenant_status,
        disabled_at=(datetime.now(UTC) if tenant_status is TenantStatus.DISABLED else None),
    )
    session.add(tenant)

    owner = create_user(
        session,
        prefix="role-owner",
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
            prefix="role-actor",
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
            prefix="role-target",
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
    ("initial_role", "requested_role"),
    [
        (TenantRole.STAFF, TenantRole.ADMIN),
        (TenantRole.ADMIN, TenantRole.STAFF),
    ],
)
def test_change_membership_role_persists_allowed_transition(
    db_session: Session,
    initial_role: TenantRole,
    requested_role: TenantRole,
) -> None:
    scenario = create_scenario(
        db_session,
        target_role=initial_role,
    )
    service = ChangeMembershipRoleService()

    result = service.execute(
        db_session,
        ChangeMembershipRoleCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=scenario.target_membership.id,
            role=requested_role,
        ),
    )

    assert result.membership_id == scenario.target_membership.id
    assert result.tenant_id == scenario.tenant.id
    assert result.user_id == scenario.target.id
    assert result.previous_role is initial_role
    assert result.role is requested_role
    assert result.updated_at == scenario.target_membership.updated_at
    assert scenario.target_membership.role is requested_role


def test_same_role_request_is_idempotent_without_flush(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        target_role=TenantRole.ADMIN,
    )
    repository = MembershipAdministrationRepository()
    service = ChangeMembershipRoleService(repository)

    with patch.object(
        repository,
        "flush_and_refresh",
        wraps=repository.flush_and_refresh,
    ) as flush_and_refresh:
        result = service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.ADMIN,
            ),
        )

    flush_and_refresh.assert_not_called()
    assert result.previous_role is TenantRole.ADMIN
    assert result.role is TenantRole.ADMIN


def test_generic_role_change_rejects_owner_assignment(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    service = ChangeMembershipRoleService()

    with pytest.raises(MembershipRoleNotAllowedError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.OWNER,
            ),
        )

    assert scenario.target_membership.role is TenantRole.STAFF


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
def test_role_change_revalidates_actor_authorization(
    db_session: Session,
    actor_role: TenantRole,
    actor_status: MembershipStatus,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=actor_role,
        actor_status=actor_status,
    )
    service = ChangeMembershipRoleService()

    with pytest.raises(MembershipActorNotAuthorizedError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.ADMIN,
            ),
        )

    assert scenario.target_membership.role is TenantRole.STAFF


def test_role_change_rejects_cross_tenant_membership(
    db_session: Session,
) -> None:
    source = create_scenario(db_session)
    foreign = create_scenario(db_session)
    service = ChangeMembershipRoleService()

    with pytest.raises(MembershipNotFoundError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=source.tenant.id,
                actor_user_id=source.actor.id,
                membership_id=foreign.target_membership.id,
                role=TenantRole.ADMIN,
            ),
        )

    assert foreign.target_membership.role is TenantRole.STAFF


def test_role_change_rejects_disabled_target(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        target_status=MembershipStatus.DISABLED,
    )
    service = ChangeMembershipRoleService()

    with pytest.raises(MembershipDisabledError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.ADMIN,
            ),
        )

    assert scenario.target_membership.role is TenantRole.STAFF


def test_role_change_protects_owner_membership(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
        target_is_owner=True,
    )
    service = ChangeMembershipRoleService()

    with pytest.raises(MembershipOwnerProtectedError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.ADMIN,
            ),
        )

    assert scenario.target_membership.role is TenantRole.OWNER


def test_role_change_rejects_administrative_self_management(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
    )
    service = ChangeMembershipRoleService()

    with pytest.raises(MembershipSelfManagementNotAllowedError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.actor_membership.id,
                role=TenantRole.STAFF,
            ),
        )

    assert scenario.actor_membership.role is TenantRole.ADMIN


def test_role_change_rejects_disabled_tenant(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        tenant_status=TenantStatus.DISABLED,
    )
    service = ChangeMembershipRoleService()

    with pytest.raises(TenantDisabledError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.ADMIN,
            ),
        )


def test_role_change_rejects_missing_tenant(
    db_session: Session,
) -> None:
    service = ChangeMembershipRoleService()

    with pytest.raises(TenantNotFoundError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=uuid4(),
                actor_user_id=uuid4(),
                membership_id=uuid4(),
                role=TenantRole.ADMIN,
            ),
        )


def test_successful_role_change_does_not_commit_transaction(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    service = ChangeMembershipRoleService()

    with patch.object(
        db_session,
        "commit",
        wraps=db_session.commit,
    ) as commit:
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.ADMIN,
            ),
        )

    commit.assert_not_called()
