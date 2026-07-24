from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
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
from clinicops.professionals.contracts import (
    UnlinkedProfessionalForMembershipRemoval,
    UnlinkProfessionalForMembershipRemovalCommand,
)
from clinicops.professionals.services.unlink_professional_for_membership_removal import (
    UnlinkProfessionalForMembershipRemovalService,
)
from clinicops.tenancy.exceptions import (
    MembershipActorNotAuthorizedError,
    MembershipNotFoundError,
    MembershipOwnerProtectedError,
    MembershipSelfManagementNotAllowedError,
    TenantDisabledError,
    TenantNotFoundError,
)
from clinicops.tenancy.membership_administration_repository import (
    LockedMembershipAdministrationState,
    MembershipAdministrationRepository,
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


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class SimulatedProfessionalUnlinkError(RuntimeError):
    """Represent a deterministic professional-unlink failure."""


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
    """Raise after the membership delete has already been flushed."""

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError


class RecordingUnlinkProfessionalForMembershipRemovalService:
    """Capture Membership-removal unlink calls without Professional persistence."""

    def __init__(
        self,
        *,
        events: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.sessions: list[Session] = []
        self.tenant_ids: list[UUID] = []
        self.membership_ids: list[UUID] = []
        self._events = events
        self._error = error

    def execute(
        self,
        session: Session,
        command: UnlinkProfessionalForMembershipRemovalCommand,
    ) -> UnlinkedProfessionalForMembershipRemoval:
        self.sessions.append(session)
        self.tenant_ids.append(command.tenant_id)
        self.membership_ids.append(command.membership_id)

        if self._events is not None:
            self._events.append("unlink")

        if self._error is not None:
            raise self._error

        return UnlinkedProfessionalForMembershipRemoval(
            professional=None,
            previous_membership_id=None,
        )


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
    removed_role = scenario.target_membership.role
    context = audit_context(user_id=scenario.actor.id)
    recorder = RecordingAuditRecorder()
    service = RemoveMembershipService(audit_recorder=recorder)

    result = service.execute(
        db_session,
        RemoveMembershipCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=target_membership_id,
            audit_context=context,
        ),
    )

    assert result.membership_id == target_membership_id
    assert result.tenant_id == scenario.tenant.id
    assert result.user_id == target_user_id
    assert db_session.get(Membership, target_membership_id) is None
    assert db_session.get(User, target_user_id) is not None
    assert recorder.sessions == [db_session]
    assert len(recorder.commands) == 1

    command = recorder.commands[0]

    assert command.action == AuditAction.MEMBERSHIP_REMOVED.value
    assert command.resource_type == AuditResourceType.MEMBERSHIP.value
    assert command.resource_id == str(target_membership_id)
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
        "target_user_id": str(target_user_id),
        "removed_role": removed_role.value,
    }
    assert "email" not in command.metadata
    assert "name" not in command.metadata


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
            audit_context=audit_context(user_id=scenario.actor.id),
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
    recorder = RecordingAuditRecorder()
    service = RemoveMembershipService(audit_recorder=recorder)

    with pytest.raises(MembershipOwnerProtectedError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                audit_context=audit_context(
                    user_id=scenario.actor.id,
                    role=TenantRole.ADMIN.value,
                ),
            ),
        )

    assert (
        db_session.get(
            Membership,
            scenario.target_membership.id,
        )
        is not None
    )
    assert recorder.commands == []


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
                audit_context=audit_context(user_id=scenario.actor.id),
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
                audit_context=audit_context(user_id=scenario.actor.id),
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
                audit_context=audit_context(user_id=source.actor.id),
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
                audit_context=audit_context(user_id=scenario.actor.id),
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
                audit_context=audit_context(user_id=uuid4()),
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
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    commit.assert_not_called()


def test_remove_membership_and_audit_commit_together(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    context = audit_context(user_id=scenario.actor.id)
    tenant_id = scenario.tenant.id
    membership_id = scenario.target_membership.id
    target_user_id = scenario.target.id
    actor_user_id = scenario.actor.id
    removed_role = scenario.target_membership.role

    RemoveMembershipService().execute(
        db_session,
        RemoveMembershipCommand(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            audit_context=context,
        ),
    )
    db_session.commit()

    try:
        with Session(get_engine()) as verification_session:
            assert verification_session.get(Membership, membership_id) is None
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == tenant_id,
                    AuditLogEntry.action == AuditAction.MEMBERSHIP_REMOVED.value,
                )
            )

        assert stored_audit is not None
        assert stored_audit.resource_id == str(membership_id)
        assert stored_audit.actor_user_id == actor_user_id
        assert stored_audit.request_id == context.request_id
        assert stored_audit.correlation_id == context.correlation_id
        assert stored_audit.event_metadata == {
            "target_user_id": str(target_user_id),
            "removed_role": removed_role.value,
        }
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
            cleanup_session.commit()


def test_audit_failure_prevents_membership_removal_commit(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    membership_id = scenario.target_membership.id
    service = RemoveMembershipService(audit_recorder=FailingAuditRecorder())

    with (
        patch.object(db_session, "commit", wraps=db_session.commit) as commit,
        patch.object(db_session, "rollback", wraps=db_session.rollback) as rollback,
        pytest.raises(SimulatedAuditRecordingError),
    ):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=membership_id,
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    commit.assert_not_called()
    rollback.assert_not_called()
    assert db_session.get(Membership, membership_id) is None


def test_successful_removal_invokes_professional_unlink_once(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    recording_unlink_service = RecordingUnlinkProfessionalForMembershipRemovalService()
    service = RemoveMembershipService(
        professional_unlink_service=cast(
            UnlinkProfessionalForMembershipRemovalService,
            recording_unlink_service,
        ),
    )

    service.execute(
        db_session,
        RemoveMembershipCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=scenario.target_membership.id,
            audit_context=audit_context(user_id=scenario.actor.id),
        ),
    )

    assert recording_unlink_service.sessions == [db_session]
    assert recording_unlink_service.tenant_ids == [scenario.tenant.id]
    assert recording_unlink_service.membership_ids == [
        scenario.target_membership.id,
    ]


def test_professional_unlink_runs_after_lock_before_membership_delete(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    events: list[str] = []
    recording_unlink_service = RecordingUnlinkProfessionalForMembershipRemovalService(
        events=events,
    )
    repository = MembershipAdministrationRepository()
    original_lock = repository.get_actor_and_target_for_update
    original_delete = repository.delete_and_flush

    def lock_and_record(
        session: Session,
        *,
        tenant_id: UUID,
        actor_user_id: UUID,
        target_membership_id: UUID,
    ) -> LockedMembershipAdministrationState:
        state = original_lock(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            target_membership_id=target_membership_id,
        )
        events.append("lock")
        return state

    def delete_and_record(
        session: Session,
        membership: Membership,
    ) -> None:
        events.append("delete")
        original_delete(session, membership)

    service = RemoveMembershipService(
        repository=repository,
        professional_unlink_service=cast(
            UnlinkProfessionalForMembershipRemovalService,
            recording_unlink_service,
        ),
    )

    with (
        patch.object(
            repository,
            "get_actor_and_target_for_update",
            side_effect=lock_and_record,
        ),
        patch.object(
            repository,
            "delete_and_flush",
            side_effect=delete_and_record,
        ),
    ):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    assert events == ["lock", "unlink", "delete"]
    assert recording_unlink_service.membership_ids == [
        scenario.target_membership.id,
    ]


def test_owner_protected_removal_does_not_invoke_professional_unlink(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
        target_is_owner=True,
    )
    recording_unlink_service = RecordingUnlinkProfessionalForMembershipRemovalService()
    service = RemoveMembershipService(
        professional_unlink_service=cast(
            UnlinkProfessionalForMembershipRemovalService,
            recording_unlink_service,
        ),
    )

    with pytest.raises(MembershipOwnerProtectedError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                audit_context=audit_context(
                    user_id=scenario.actor.id,
                    role=TenantRole.ADMIN.value,
                ),
            ),
        )

    assert recording_unlink_service.sessions == []
    assert recording_unlink_service.membership_ids == []


def test_self_removal_rejection_does_not_invoke_professional_unlink(
    db_session: Session,
) -> None:
    scenario = create_scenario(
        db_session,
        actor_role=TenantRole.ADMIN,
    )
    recording_unlink_service = RecordingUnlinkProfessionalForMembershipRemovalService()
    service = RemoveMembershipService(
        professional_unlink_service=cast(
            UnlinkProfessionalForMembershipRemovalService,
            recording_unlink_service,
        ),
    )

    with pytest.raises(MembershipSelfManagementNotAllowedError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.actor_membership.id,
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    assert recording_unlink_service.sessions == []
    assert recording_unlink_service.membership_ids == []


@pytest.mark.parametrize(
    "membership_source",
    [
        "missing",
        "cross_tenant",
    ],
)
def test_missing_or_cross_tenant_membership_does_not_invoke_professional_unlink(
    db_session: Session,
    membership_source: str,
) -> None:
    source = create_scenario(db_session)
    foreign = create_scenario(db_session)
    recording_unlink_service = RecordingUnlinkProfessionalForMembershipRemovalService()
    service = RemoveMembershipService(
        professional_unlink_service=cast(
            UnlinkProfessionalForMembershipRemovalService,
            recording_unlink_service,
        ),
    )
    membership_id = uuid4() if membership_source == "missing" else foreign.target_membership.id

    with pytest.raises(MembershipNotFoundError):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=source.tenant.id,
                actor_user_id=source.actor.id,
                membership_id=membership_id,
                audit_context=audit_context(user_id=source.actor.id),
            ),
        )

    assert recording_unlink_service.sessions == []
    assert recording_unlink_service.membership_ids == []


def test_professional_unlink_failure_prevents_membership_delete(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    membership_id = scenario.target_membership.id
    recording_unlink_service = RecordingUnlinkProfessionalForMembershipRemovalService(
        error=SimulatedProfessionalUnlinkError("unlink unavailable"),
    )
    repository = MembershipAdministrationRepository()
    service = RemoveMembershipService(
        repository=repository,
        professional_unlink_service=cast(
            UnlinkProfessionalForMembershipRemovalService,
            recording_unlink_service,
        ),
    )

    with (
        patch.object(
            repository,
            "delete_and_flush",
            wraps=repository.delete_and_flush,
        ) as delete_and_flush,
        pytest.raises(SimulatedProfessionalUnlinkError),
    ):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=membership_id,
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    delete_and_flush.assert_not_called()
    assert recording_unlink_service.sessions == [db_session]
    assert recording_unlink_service.membership_ids == [membership_id]
    assert db_session.get(Membership, membership_id) is not None


def test_membership_removed_audit_unchanged_with_professional_unlink(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    target_user_id = scenario.target.id
    target_membership_id = scenario.target_membership.id
    removed_role = scenario.target_membership.role
    context = audit_context(user_id=scenario.actor.id)
    recorder = RecordingAuditRecorder()
    recording_unlink_service = RecordingUnlinkProfessionalForMembershipRemovalService()
    service = RemoveMembershipService(
        audit_recorder=recorder,
        professional_unlink_service=cast(
            UnlinkProfessionalForMembershipRemovalService,
            recording_unlink_service,
        ),
    )

    result = service.execute(
        db_session,
        RemoveMembershipCommand(
            tenant_id=scenario.tenant.id,
            actor_user_id=scenario.actor.id,
            membership_id=target_membership_id,
            audit_context=context,
        ),
    )

    assert result.membership_id == target_membership_id
    assert recording_unlink_service.membership_ids == [target_membership_id]
    assert recorder.sessions == [db_session]
    assert len(recorder.commands) == 1

    command = recorder.commands[0]

    assert command.action == AuditAction.MEMBERSHIP_REMOVED.value
    assert command.resource_type == AuditResourceType.MEMBERSHIP.value
    assert command.resource_id == str(target_membership_id)
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
        "target_user_id": str(target_user_id),
        "removed_role": removed_role.value,
    }
    assert "email" not in command.metadata
    assert "name" not in command.metadata


def test_removal_with_professional_unlink_does_not_commit_or_rollback(
    db_session: Session,
) -> None:
    scenario = create_scenario(db_session)
    recording_unlink_service = RecordingUnlinkProfessionalForMembershipRemovalService()
    service = RemoveMembershipService(
        professional_unlink_service=cast(
            UnlinkProfessionalForMembershipRemovalService,
            recording_unlink_service,
        ),
    )

    with (
        patch.object(db_session, "commit", wraps=db_session.commit) as commit,
        patch.object(db_session, "rollback", wraps=db_session.rollback) as rollback,
    ):
        service.execute(
            db_session,
            RemoveMembershipCommand(
                tenant_id=scenario.tenant.id,
                actor_user_id=scenario.actor.id,
                membership_id=scenario.target_membership.id,
                audit_context=audit_context(user_id=scenario.actor.id),
            ),
        )

    commit.assert_not_called()
    rollback.assert_not_called()
    assert recording_unlink_service.sessions == [db_session]
