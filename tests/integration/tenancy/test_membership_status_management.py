from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.core.clock import Clock
from clinicops.db.session import get_engine
from clinicops.identity.models import User, UserStatus
from clinicops.tenancy.exceptions import (
    MembershipActorNotAuthorizedError,
    MembershipAlreadyActiveError,
    MembershipAlreadyDisabledError,
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
from clinicops.tenancy.services.disable_membership import (
    DisableMembershipService,
)
from clinicops.tenancy.services.enable_membership import (
    EnableMembershipService,
)
from clinicops.tenancy.services.membership_administration import (
    DisableMembershipCommand,
    EnableMembershipCommand,
)

FIXED_NOW = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


class FixedClock:
    """Return one deterministic membership lifecycle timestamp."""

    def now(self) -> datetime:
        return FIXED_NOW


@dataclass(frozen=True, slots=True)
class MembershipScenario:
    """Persisted membership lifecycle state."""

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
    target_is_active_owner: bool = False,
    tenant_status: TenantStatus = TenantStatus.ACTIVE,
) -> MembershipScenario:
    """Persist one tenant with owner, actor, and target memberships."""

    tenant = Tenant(
        name=f"Membership Status Clinic {uuid4().hex[:8]}",
        status=tenant_status,
        disabled_at=(datetime.now(UTC) if tenant_status is TenantStatus.DISABLED else None),
    )
    session.add(tenant)

    owner = create_user(
        session,
        prefix="status-owner",
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
            prefix="status-actor",
        )
        actor_membership = Membership(
            tenant=tenant,
            user=actor,
            role=actor_role,
            status=actor_status,
            disabled_at=(FIXED_NOW if actor_status is MembershipStatus.DISABLED else None),
        )
        session.add(actor_membership)

    if target_is_active_owner:
        target = owner
        target_membership = owner_membership
    else:
        target = create_user(
            session,
            prefix="status-target",
        )
        target_membership = Membership(
            tenant=tenant,
            user=target,
            role=target_role,
            status=target_status,
            disabled_at=(FIXED_NOW if target_status is MembershipStatus.DISABLED else None),
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
    "target_role",
    [
        TenantRole.ADMIN,
        TenantRole.STAFF,
    ],
)
def test_disable_membership_persists_status_and_timestamp(
    db_session: Session,
    target_role: TenantRole,
) -> None:
    scenario = create_scenario(
        db_session,
        target_role=target_role,
    )
    service = DisableMembershipService(
        clock=cast(Clock, FixedClock()),
    )

    result = service.execute(
        db_session,
        DisableMembershipCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=scenario.target_membership.id,
        ),
    )

    assert result.membership_id == scenario.target_membership.id
    assert result.tenant_id == scenario.tenant.id
    assert result.user_id == scenario.target.id
    assert result.role is target_role
    assert result.disabled_at == FIXED_NOW
    assert scenario.target_membership.status is MembershipStatus.DISABLED
    assert scenario.target_membership.disabled_at == FIXED_NOW
    assert scenario.target_membership.role is target_role


def test_disable_membership_rejects_already_disabled_target(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        target_status=MembershipStatus.DISABLED,
    )
    service = DisableMembershipService(
        clock=cast(Clock, FixedClock()),
    )

    with pytest.raises(MembershipAlreadyDisabledError):
        service.execute(
            db_session,
            DisableMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    assert scenario.target_membership.status is MembershipStatus.DISABLED


def test_disable_membership_protects_owner_role(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
        target_is_active_owner=True,
    )
    service = DisableMembershipService(
        clock=cast(Clock, FixedClock()),
    )

    with pytest.raises(MembershipOwnerProtectedError):
        service.execute(
            db_session,
            DisableMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )


def test_disable_membership_rejects_self_management(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
    )
    service = DisableMembershipService(
        clock=cast(Clock, FixedClock()),
    )

    with pytest.raises(MembershipSelfManagementNotAllowedError):
        service.execute(
            db_session,
            DisableMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.actor_membership.id,
            ),
        )

    assert scenario.actor_membership.status is MembershipStatus.ACTIVE


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
def test_disable_membership_revalidates_actor_authorization(
    db_session: Session,
    actor_role: TenantRole,
    actor_status: MembershipStatus,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=actor_role,
        actor_status=actor_status,
    )
    service = DisableMembershipService(
        clock=cast(Clock, FixedClock()),
    )

    with pytest.raises(MembershipActorNotAuthorizedError):
        service.execute(
            db_session,
            DisableMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    assert scenario.target_membership.status is MembershipStatus.ACTIVE


def test_disable_membership_rejects_cross_tenant_target(
    db_session: Session,
) -> None:
    source = create_scenario(db_session)
    foreign = create_scenario(db_session)
    service = DisableMembershipService(
        clock=cast(Clock, FixedClock()),
    )

    with pytest.raises(MembershipNotFoundError):
        service.execute(
            db_session,
            DisableMembershipCommand(
                tenant_id=source.tenant.id,
                actor_user_id=source.actor.id,
                membership_id=foreign.target_membership.id,
            ),
        )

    assert foreign.target_membership.status is MembershipStatus.ACTIVE


@pytest.mark.parametrize(
    "target_role",
    [
        TenantRole.ADMIN,
        TenantRole.STAFF,
    ],
)
def test_enable_membership_restores_access_and_preserves_role(
    db_session: Session,
    target_role: TenantRole,
) -> None:
    scenario = create_scenario(
        db_session,
        target_role=target_role,
        target_status=MembershipStatus.DISABLED,
    )
    previous_updated_at = scenario.target_membership.updated_at
    service = EnableMembershipService()

    result = service.execute(
        db_session,
        EnableMembershipCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=scenario.target_membership.id,
        ),
    )

    assert result.membership_id == scenario.target_membership.id
    assert result.tenant_id == scenario.tenant.id
    assert result.user_id == scenario.target.id
    assert result.role is target_role
    assert result.updated_at == scenario.target_membership.updated_at
    assert result.updated_at >= previous_updated_at
    assert scenario.target_membership.status is MembershipStatus.ACTIVE
    assert scenario.target_membership.disabled_at is None
    assert scenario.target_membership.role is target_role


def test_enable_membership_rejects_already_active_target(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    service = EnableMembershipService()

    with pytest.raises(MembershipAlreadyActiveError):
        service.execute(
            db_session,
            EnableMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    assert scenario.target_membership.status is MembershipStatus.ACTIVE


def test_enable_membership_protects_disabled_owner_role(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        target_role=TenantRole.OWNER,
        target_status=MembershipStatus.DISABLED,
    )
    service = EnableMembershipService()

    with pytest.raises(MembershipOwnerProtectedError):
        service.execute(
            db_session,
            EnableMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    assert scenario.target_membership.status is MembershipStatus.DISABLED


def test_enable_membership_rejects_self_management(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
    )
    service = EnableMembershipService()

    with pytest.raises(MembershipSelfManagementNotAllowedError):
        service.execute(
            db_session,
            EnableMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.actor_membership.id,
            ),
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
def test_enable_membership_revalidates_actor_authorization(
    db_session: Session,
    actor_role: TenantRole,
    actor_status: MembershipStatus,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=actor_role,
        actor_status=actor_status,
        target_status=MembershipStatus.DISABLED,
    )
    service = EnableMembershipService()

    with pytest.raises(MembershipActorNotAuthorizedError):
        service.execute(
            db_session,
            EnableMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
            ),
        )

    assert scenario.target_membership.status is MembershipStatus.DISABLED


def test_enable_membership_rejects_cross_tenant_target(
    db_session: Session,
) -> None:
    source = create_scenario(db_session)
    foreign = create_scenario(
        db_session,
        target_status=MembershipStatus.DISABLED,
    )
    service = EnableMembershipService()

    with pytest.raises(MembershipNotFoundError):
        service.execute(
            db_session,
            EnableMembershipCommand(
                tenant_id=source.tenant.id,
                actor_user_id=source.actor.id,
                membership_id=foreign.target_membership.id,
            ),
        )

    assert foreign.target_membership.status is MembershipStatus.DISABLED


@pytest.mark.parametrize(
    "service_kind",
    ["disable", "enable"],
)
def test_status_management_rejects_disabled_tenant(
    db_session: Session,
    service_kind: str,
) -> None:
    target_status = (
        MembershipStatus.ACTIVE if service_kind == "disable" else MembershipStatus.DISABLED
    )
    scenario = create_scenario(
        db_session,
        target_status=target_status,
        tenant_status=TenantStatus.DISABLED,
    )

    with pytest.raises(TenantDisabledError):
        if service_kind == "disable":
            DisableMembershipService(
                clock=cast(Clock, FixedClock()),
            ).execute(
                db_session,
                DisableMembershipCommand(
                    tenant_id=scenario.tenant.id,
                    actor_user_id=scenario.actor.id,
                    membership_id=scenario.target_membership.id,
                ),
            )
        else:
            EnableMembershipService().execute(
                db_session,
                EnableMembershipCommand(
                    tenant_id=scenario.tenant.id,
                    actor_user_id=scenario.actor.id,
                    membership_id=scenario.target_membership.id,
                ),
            )


@pytest.mark.parametrize(
    "service_kind",
    ["disable", "enable"],
)
def test_status_management_rejects_missing_tenant(
    db_session: Session,
    service_kind: str,
) -> None:
    with pytest.raises(TenantNotFoundError):
        if service_kind == "disable":
            DisableMembershipService(
                clock=cast(Clock, FixedClock()),
            ).execute(
                db_session,
                DisableMembershipCommand(
                    tenant_id=uuid4(),
                    actor_user_id=uuid4(),
                    membership_id=uuid4(),
                ),
            )
        else:
            EnableMembershipService().execute(
                db_session,
                EnableMembershipCommand(
                    tenant_id=uuid4(),
                    actor_user_id=uuid4(),
                    membership_id=uuid4(),
                ),
            )


def test_status_management_services_do_not_commit(
    db_session: Session,
) -> None:
    disable_scenario = create_scenario(db_session)
    enable_scenario = create_scenario(
        db_session,
        target_status=MembershipStatus.DISABLED,
    )

    with patch.object(
        db_session,
        "commit",
        wraps=db_session.commit,
    ) as commit:
        DisableMembershipService(
            clock=cast(Clock, FixedClock()),
        ).execute(
            db_session,
            DisableMembershipCommand(
                tenant_id=disable_scenario.tenant.id,
                actor_user_id=disable_scenario.actor.id,
                membership_id=(disable_scenario.target_membership.id),
            ),
        )
        EnableMembershipService().execute(
            db_session,
            EnableMembershipCommand(
                tenant_id=enable_scenario.tenant.id,
                actor_user_id=enable_scenario.actor.id,
                membership_id=(enable_scenario.target_membership.id),
            ),
        )

    commit.assert_not_called()
