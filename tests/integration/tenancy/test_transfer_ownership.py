from collections.abc import Iterator
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
    """Raise after ownership mutations have already been flushed."""

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
    context = audit_context(user_id=current_owner.user_id)
    recorder = RecordingAuditRecorder()
    service = TransferTenantOwnershipService(audit_recorder=recorder)

    result = service.execute(
        db_session,
        TransferTenantOwnershipCommand(
            tenant_id=tenant.id,
            expected_current_owner_user_id=current_owner.user_id,
            new_owner_user_id=target_membership.user_id,
            audit_context=context,
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
    assert recorder.sessions == [db_session]
    assert len(recorder.commands) == 1

    command = recorder.commands[0]

    assert command.action == AuditAction.TENANT_OWNERSHIP_TRANSFERRED.value
    assert command.resource_type == AuditResourceType.TENANT.value
    assert command.resource_id == str(tenant.id)
    assert command.tenant_id == tenant.id
    assert command.actor.actor_type is AuditActorType.USER
    assert command.actor.user_id == current_owner.user_id
    assert command.actor.role == TenantRole.OWNER.value
    assert command.source is AuditSource.HTTP
    assert command.request_id == context.request_id
    assert command.correlation_id == context.correlation_id
    assert command.metadata_version == 1
    assert command.idempotency_key is None
    assert command.metadata == {
        "previous_owner_user_id": str(current_owner.user_id),
        "new_owner_user_id": str(target_membership.user_id),
    }
    assert "email" not in command.metadata


def test_transfer_rejects_current_owner_as_target(
    db_session: Session,
) -> None:
    tenant, current_owner, _ = create_transfer_fixture(db_session)
    recorder = RecordingAuditRecorder()
    service = TransferTenantOwnershipService(audit_recorder=recorder)

    with pytest.raises(InvalidOwnershipTransferError):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=current_owner.user_id,
                new_owner_user_id=current_owner.user_id,
                audit_context=audit_context(user_id=current_owner.user_id),
            ),
        )

    assert current_owner.role is TenantRole.OWNER
    assert recorder.commands == []


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
                audit_context=audit_context(user_id=current_owner.user_id),
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
                audit_context=audit_context(user_id=current_owner.user_id),
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
                audit_context=audit_context(user_id=current_owner.user_id),
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
                audit_context=audit_context(user_id=target_membership.user_id),
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
                audit_context=audit_context(user_id=uuid4()),
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
                audit_context=audit_context(user_id=current_owner.user_id),
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
                audit_context=audit_context(user_id=uuid4()),
            ),
        )


def test_ownership_transfer_and_audit_commit_together(
    db_session: Session,
) -> None:
    tenant, current_owner, target_membership = create_transfer_fixture(db_session)
    context = audit_context(user_id=current_owner.user_id)
    tenant_id = tenant.id
    previous_owner_user_id = current_owner.user_id
    new_owner_user_id = target_membership.user_id

    TransferTenantOwnershipService().execute(
        db_session,
        TransferTenantOwnershipCommand(
            tenant_id=tenant_id,
            expected_current_owner_user_id=previous_owner_user_id,
            new_owner_user_id=new_owner_user_id,
            audit_context=context,
        ),
    )
    db_session.commit()

    try:
        with Session(get_engine()) as verification_session:
            roles = {
                membership.user_id: membership.role
                for membership in verification_session.scalars(
                    select(Membership).where(Membership.tenant_id == tenant_id)
                )
            }
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == tenant_id,
                    AuditLogEntry.action == AuditAction.TENANT_OWNERSHIP_TRANSFERRED.value,
                )
            )

        assert roles[previous_owner_user_id] is TenantRole.ADMIN
        assert roles[new_owner_user_id] is TenantRole.OWNER
        assert stored_audit is not None
        assert stored_audit.resource_id == str(tenant_id)
        assert stored_audit.actor_user_id == previous_owner_user_id
        assert stored_audit.request_id == context.request_id
        assert stored_audit.correlation_id == context.correlation_id
        assert stored_audit.event_metadata == {
            "previous_owner_user_id": str(previous_owner_user_id),
            "new_owner_user_id": str(new_owner_user_id),
        }
    finally:
        with Session(get_engine()) as cleanup_session:
            cleanup_session.execute(
                delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id)
            )
            cleanup_session.execute(delete(Membership).where(Membership.tenant_id == tenant_id))
            cleanup_session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            cleanup_session.execute(
                delete(User).where(User.id.in_([previous_owner_user_id, new_owner_user_id]))
            )
            cleanup_session.commit()


def test_audit_failure_prevents_ownership_transfer_commit(
    db_session: Session,
) -> None:
    tenant, current_owner, target_membership = create_transfer_fixture(db_session)
    service = TransferTenantOwnershipService(
        audit_recorder=FailingAuditRecorder(),
    )

    with (
        patch.object(db_session, "commit", wraps=db_session.commit) as commit,
        patch.object(db_session, "rollback", wraps=db_session.rollback) as rollback,
        pytest.raises(SimulatedAuditRecordingError),
    ):
        service.execute(
            db_session,
            TransferTenantOwnershipCommand(
                tenant_id=tenant.id,
                expected_current_owner_user_id=current_owner.user_id,
                new_owner_user_id=target_membership.user_id,
                audit_context=audit_context(user_id=current_owner.user_id),
            ),
        )

    commit.assert_not_called()
    rollback.assert_not_called()
    assert current_owner.role is TenantRole.ADMIN
    assert target_membership.role is TenantRole.OWNER
