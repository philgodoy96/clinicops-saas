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


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class FailingTenantRepository(TenantRepository):
    """Flush the aggregate and then simulate a later workflow failure."""

    def add_and_flush(
        self,
        session: Session,
        tenant: Tenant,
    ) -> None:
        super().add_and_flush(session, tenant)
        raise SimulatedTenantCreationError


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
    """Raise after the domain mutation has already been flushed."""

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError


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


def delete_tenant_and_audit(tenant_id: UUID) -> None:
    """Remove a committed tenant and its audit entries."""

    with Session(get_engine()) as session:
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def audit_context(
    *,
    user_id: UUID,
    role: str = TenantRole.OWNER.value,
) -> AuditRecordingContext:
    """Build one immutable HTTP audit context for tenant creation."""

    return AuditRecordingContext.http_user(
        user_id=user_id,
        role=role,
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
    )


def test_create_tenant_persists_normalized_name_and_initial_owner(
    db_session: Session,
) -> None:
    owner_user = create_user("tenant-owner")
    db_session.add(owner_user)
    db_session.flush()
    recorder = RecordingAuditRecorder()

    service = CreateTenantService(audit_recorder=recorder)
    created_tenant = service.execute(
        db_session,
        CreateTenantCommand(
            name="  Northstar Health Clinic  ",
            owner_user_id=owner_user.id,
            audit_context=audit_context(user_id=owner_user.id),
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

    assert recorder.sessions == [db_session]
    assert len(recorder.commands) == 1

    command = recorder.commands[0]

    assert command.action == AuditAction.TENANT_CREATED.value
    assert command.resource_type == AuditResourceType.TENANT.value
    assert command.resource_id == str(created_tenant.id)
    assert command.tenant_id == created_tenant.id
    assert command.actor.actor_type is AuditActorType.USER
    assert command.actor.user_id == owner_user.id
    assert command.actor.role == TenantRole.OWNER.value
    assert command.source is AuditSource.HTTP
    assert command.metadata_version == 1
    assert command.metadata == {
        "tenant_name": "Northstar Health Clinic",
    }
    assert command.idempotency_key == f"tenant-created:{created_tenant.id}"
    assert "email" not in command.metadata
    assert "idempotency_key" not in command.metadata


def test_create_tenant_rejects_missing_owner_user(
    db_session: Session,
) -> None:
    tenant_name = f"Missing Owner Clinic {uuid4()}"
    recorder = RecordingAuditRecorder()
    service = CreateTenantService(audit_recorder=recorder)
    missing_owner_id = uuid4()

    with pytest.raises(UserNotFoundError):
        service.execute(
            db_session,
            CreateTenantCommand(
                name=tenant_name,
                owner_user_id=missing_owner_id,
                audit_context=audit_context(user_id=missing_owner_id),
            ),
        )

    stored_tenant = db_session.scalar(select(Tenant).where(Tenant.name == tenant_name))

    assert stored_tenant is None
    assert recorder.commands == []


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
    recorder = RecordingAuditRecorder()
    service = CreateTenantService(audit_recorder=recorder)

    with pytest.raises(UserDisabledError):
        service.execute(
            db_session,
            CreateTenantCommand(
                name=tenant_name,
                owner_user_id=owner_user.id,
                audit_context=audit_context(user_id=owner_user.id),
            ),
        )

    stored_tenant = db_session.scalar(select(Tenant).where(Tenant.name == tenant_name))

    assert stored_tenant is None
    assert recorder.commands == []


def test_create_tenant_does_not_commit_the_transaction() -> None:
    owner_user = create_user("rollback-owner")
    owner_user_id = persist_user(owner_user)
    tenant_name = f"Rollback Clinic {uuid4()}"
    context = audit_context(user_id=owner_user_id)

    try:
        with Session(get_engine()) as session:
            with (
                patch.object(
                    session,
                    "commit",
                    wraps=session.commit,
                ) as commit,
                patch.object(
                    session,
                    "rollback",
                    wraps=session.rollback,
                ) as rollback,
            ):
                CreateTenantService().execute(
                    session,
                    CreateTenantCommand(
                        name=tenant_name,
                        owner_user_id=owner_user_id,
                        audit_context=context,
                    ),
                )

            commit.assert_not_called()
            rollback.assert_not_called()
            session.rollback()

        with Session(get_engine()) as verification_session:
            stored_tenant = verification_session.scalar(
                select(Tenant).where(Tenant.name == tenant_name)
            )
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.correlation_id == context.correlation_id,
                    AuditLogEntry.action == AuditAction.TENANT_CREATED.value,
                )
            )

        assert stored_tenant is None
        assert stored_audit is None
    finally:
        delete_user(owner_user_id)


def test_later_failure_rolls_back_tenant_and_owner_membership() -> None:
    owner_user = create_user("atomic-owner")
    owner_user_id = persist_user(owner_user)
    tenant_name = f"Atomic Clinic {uuid4()}"
    recorder = RecordingAuditRecorder()
    service = CreateTenantService(
        tenant_repository=FailingTenantRepository(),
        audit_recorder=recorder,
    )

    try:
        with Session(get_engine()) as session:
            with pytest.raises(SimulatedTenantCreationError):
                service.execute(
                    session,
                    CreateTenantCommand(
                        name=tenant_name,
                        owner_user_id=owner_user_id,
                        audit_context=audit_context(user_id=owner_user_id),
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
        assert recorder.commands == []
    finally:
        delete_user(owner_user_id)


def test_create_tenant_and_audit_commit_together() -> None:
    owner_user = create_user("audit-commit-owner")
    owner_user_id = persist_user(owner_user)
    tenant_name = f"Audit Commit Clinic {uuid4()}"
    context = audit_context(user_id=owner_user_id)
    tenant_id: UUID | None = None

    try:
        with Session(get_engine()) as session:
            created = CreateTenantService().execute(
                session,
                CreateTenantCommand(
                    name=tenant_name,
                    owner_user_id=owner_user_id,
                    audit_context=context,
                ),
            )
            tenant_id = created.id
            session.commit()

        with Session(get_engine()) as verification_session:
            stored_tenant = verification_session.get(Tenant, tenant_id)
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.tenant_id == tenant_id,
                    AuditLogEntry.action == AuditAction.TENANT_CREATED.value,
                )
            )

        assert stored_tenant is not None
        assert stored_audit is not None
        assert stored_audit.resource_type == AuditResourceType.TENANT.value
        assert stored_audit.resource_id == str(tenant_id)
        assert stored_audit.actor_user_id == owner_user_id
        assert stored_audit.actor_role == TenantRole.OWNER.value
        assert stored_audit.source == AuditSource.HTTP.value
        assert stored_audit.request_id == context.request_id
        assert stored_audit.correlation_id == context.correlation_id
        assert stored_audit.metadata_version == 1
        assert stored_audit.event_metadata == {
            "tenant_name": stored_tenant.name,
        }
        assert stored_audit.idempotency_key == f"tenant-created:{tenant_id}"
    finally:
        if tenant_id is not None:
            delete_tenant_and_audit(tenant_id)
        delete_user(owner_user_id)


def test_audit_failure_prevents_tenant_creation_commit() -> None:
    owner_user = create_user("audit-fail-owner")
    owner_user_id = persist_user(owner_user)
    tenant_name = f"Audit Fail Clinic {uuid4()}"
    context = audit_context(user_id=owner_user_id)
    service = CreateTenantService(audit_recorder=FailingAuditRecorder())

    try:
        with Session(get_engine()) as session:
            with (
                patch.object(
                    session,
                    "commit",
                    wraps=session.commit,
                ) as commit,
                patch.object(
                    session,
                    "rollback",
                    wraps=session.rollback,
                ) as rollback,
                pytest.raises(SimulatedAuditRecordingError),
            ):
                service.execute(
                    session,
                    CreateTenantCommand(
                        name=tenant_name,
                        owner_user_id=owner_user_id,
                        audit_context=context,
                    ),
                )

            commit.assert_not_called()
            rollback.assert_not_called()
            session.rollback()

        with Session(get_engine()) as verification_session:
            stored_tenant = verification_session.scalar(
                select(Tenant).where(Tenant.name == tenant_name)
            )
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.correlation_id == context.correlation_id,
                    AuditLogEntry.action == AuditAction.TENANT_CREATED.value,
                )
            )

        assert stored_tenant is None
        assert stored_audit is None
    finally:
        delete_user(owner_user_id)


def test_create_tenant_command_requires_immutable_audit_context() -> None:
    from dataclasses import FrozenInstanceError

    user_id = uuid4()
    context = audit_context(user_id=user_id)
    command = CreateTenantCommand(
        name="Northstar Health Clinic",
        owner_user_id=user_id,
        audit_context=context,
    )

    assert command.audit_context is context

    with pytest.raises(FrozenInstanceError):
        command.audit_context = audit_context(user_id=user_id)  # type: ignore[misc]
