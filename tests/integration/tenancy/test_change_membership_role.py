from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import (
    RecordAuditLogCommand,
    RecordedAuditLog,
)
from clinicops.audit.enums import AuditActorType, AuditSource
from clinicops.audit.models import AuditLogEntry
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


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class RecordingAuditRecorder:
    """Capture audit commands without touching the database."""

    def __init__(self) -> None:
        self.sessions: list[Session] = []
        self.commands: list[RecordAuditLogCommand] = []

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        self.sessions.append(session)
        self.commands.append(command)

        return RecordedAuditLog(
            audit_log_id=uuid4(),
            created=True,
            recorded_at=datetime.now(UTC),
        )


class FailingAuditRecorder:
    """Raise after the role mutation has already been flushed."""

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError


def audit_context(
    *,
    user_id: UUID,
    role: str = TenantRole.OWNER.value,
) -> AuditRecordingContext:
    """Build one immutable HTTP audit context."""

    return AuditRecordingContext.http_user(
        user_id=user_id,
        role=role,
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
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
    context = audit_context(user_id=scenario.actor.id)
    recorder = RecordingAuditRecorder()
    service = ChangeMembershipRoleService(audit_recorder=recorder)

    result = service.execute(
        db_session,
        ChangeMembershipRoleCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=scenario.target_membership.id,
            role=requested_role,
            audit_context=context,
        ),
    )

    assert result.membership_id == scenario.target_membership.id
    assert result.tenant_id == scenario.tenant.id
    assert result.user_id == scenario.target.id
    assert result.previous_role is initial_role
    assert result.role is requested_role
    assert result.updated_at == scenario.target_membership.updated_at
    assert scenario.target_membership.role is requested_role
    assert recorder.sessions == [db_session]
    assert len(recorder.commands) == 1

    command = recorder.commands[0]

    assert command.action == AuditAction.MEMBERSHIP_ROLE_CHANGED.value
    assert command.resource_type == AuditResourceType.MEMBERSHIP.value
    assert command.resource_id == str(scenario.target_membership.id)
    assert command.tenant_id == scenario.tenant.id
    assert command.actor.actor_type is AuditActorType.USER
    assert command.actor.user_id == scenario.actor.id
    assert command.actor.role == TenantRole.OWNER.value
    assert command.source is AuditSource.HTTP
    assert command.request_id == context.request_id
    assert command.correlation_id == context.correlation_id
    assert command.metadata_version == 1
    assert command.idempotency_key is None
    assert command.metadata == {
        "target_user_id": str(scenario.target.id),
        "previous_role": initial_role.value,
        "new_role": requested_role.value,
    }
    assert "email" not in command.metadata
    assert "idempotency_key" not in command.metadata


def test_same_role_request_is_idempotent_without_flush(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        target_role=TenantRole.ADMIN,
    )
    repository = MembershipAdministrationRepository()
    recorder = RecordingAuditRecorder()
    service = ChangeMembershipRoleService(
        repository,
        audit_recorder=recorder,
    )

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
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    flush_and_refresh.assert_not_called()
    assert result.previous_role is TenantRole.ADMIN
    assert result.role is TenantRole.ADMIN
    assert recorder.commands == []


def test_generic_role_change_rejects_owner_assignment(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    recorder = RecordingAuditRecorder()
    service = ChangeMembershipRoleService(audit_recorder=recorder)

    with pytest.raises(MembershipRoleNotAllowedError):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.OWNER,
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    assert scenario.target_membership.role is TenantRole.STAFF
    assert recorder.commands == []


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
                audit_context=audit_context(user_id=scenario.actor.id),
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
                audit_context=audit_context(user_id=source.actor.id),
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
                audit_context=audit_context(user_id=scenario.actor.id),
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
                audit_context=audit_context(user_id=scenario.actor.id),
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
                audit_context=audit_context(user_id=scenario.actor.id),
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
                audit_context=audit_context(user_id=scenario.actor.id),
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
                audit_context=audit_context(user_id=uuid4()),
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
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    commit.assert_not_called()


def test_role_change_and_audit_commit_together(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        target_role=TenantRole.STAFF,
    )
    context = audit_context(user_id=scenario.actor.id)
    tenant_id = scenario.tenant.id
    membership_id = scenario.target_membership.id
    target_user_id = scenario.target.id
    actor_user_id = scenario.actor.id

    ChangeMembershipRoleService().execute(
        db_session,
        ChangeMembershipRoleCommand(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            role=TenantRole.ADMIN,
            audit_context=context,
        ),
    )
    db_session.commit()

    try:
        with Session(get_engine()) as verification_session:
            membership = verification_session.get(Membership, membership_id)
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == tenant_id,
                    AuditLogEntry.action == AuditAction.MEMBERSHIP_ROLE_CHANGED.value,
                )
            )

        assert membership is not None
        assert membership.role is TenantRole.ADMIN
        assert stored_audit is not None
        assert stored_audit.resource_id == str(membership_id)
        assert stored_audit.actor_user_id == actor_user_id
        assert stored_audit.request_id == context.request_id
        assert stored_audit.correlation_id == context.correlation_id
        assert stored_audit.event_metadata == {
            "target_user_id": str(target_user_id),
            "previous_role": TenantRole.STAFF.value,
            "new_role": TenantRole.ADMIN.value,
        }
        assert stored_audit.idempotency_key is None
    finally:
        with Session(get_engine()) as cleanup_session:
            cleanup_session.execute(
                delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id)
            )
            cleanup_session.execute(delete(Membership).where(Membership.tenant_id == tenant_id))
            cleanup_session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            cleanup_session.execute(
                delete(User).where(User.id.in_([actor_user_id, target_user_id]))
            )
            # owner may equal actor
            cleanup_session.commit()


def test_audit_failure_prevents_role_change_commit(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    service = ChangeMembershipRoleService(
        audit_recorder=FailingAuditRecorder(),
    )

    with (
        patch.object(db_session, "commit", wraps=db_session.commit) as commit,
        patch.object(db_session, "rollback", wraps=db_session.rollback) as rollback,
        pytest.raises(SimulatedAuditRecordingError),
    ):
        service.execute(
            db_session,
            ChangeMembershipRoleCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                role=TenantRole.ADMIN,
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    commit.assert_not_called()
    rollback.assert_not_called()
    assert scenario.target_membership.role is TenantRole.ADMIN
