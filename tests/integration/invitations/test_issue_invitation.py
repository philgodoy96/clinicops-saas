from collections.abc import Iterator
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import (
    JSONObject,
    RecordAuditLogCommand,
    RecordedAuditLog,
)
from clinicops.audit.enums import AuditActorType, AuditSource
from clinicops.audit.models import AuditLogEntry
from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.invitations.exceptions import (
    InvitationAlreadyPendingError,
    InvitationIssuerNotAuthorizedError,
    InvitationMembershipAlreadyExistsError,
    InvitationRoleNotAllowedError,
)
from clinicops.invitations.models import Invitation, InvitationStatus
from clinicops.invitations.services.issue_invitation import (
    INVITATION_EXPIRATION,
    IssueInvitationCommand,
    IssueInvitationService,
)
from clinicops.invitations.tokens import (
    InvitationToken,
    digest_invitation_token,
)
from clinicops.tenancy.exceptions import (
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

FIXED_NOW = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)
FIXED_PLAINTEXT_TOKEN = "fixed-url-safe-invitation-token"
FIXED_TOKEN = InvitationToken(
    plaintext=FIXED_PLAINTEXT_TOKEN,
    digest=digest_invitation_token(FIXED_PLAINTEXT_TOKEN),
)


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


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
    """Raise after the invitation has already been flushed."""

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError


def fixed_token_factory() -> InvitationToken:
    """Return a deterministic invitation token."""

    return FIXED_TOKEN


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
class CommittedIssuerFixture:
    """Identifiers for a committed tenant issuer fixture."""

    tenant_id: UUID
    issuer_user_id: UUID


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def create_tenant_with_issuer(
    db_session: Session,
    *,
    issuer_role: TenantRole = TenantRole.OWNER,
    issuer_status: MembershipStatus = MembershipStatus.ACTIVE,
    tenant_status: TenantStatus = TenantStatus.ACTIVE,
) -> tuple[Tenant, User, Membership]:
    """Persist a tenant with one issuer membership."""

    issuer_user = User(
        email=f"issuer-{uuid4()}@example.com",
    )
    tenant = Tenant(
        name=f"Northstar Health Clinic {uuid4()}",
        status=tenant_status,
        disabled_at=(FIXED_NOW if tenant_status is TenantStatus.DISABLED else None),
    )
    issuer_membership = Membership(
        tenant=tenant,
        user=issuer_user,
        role=issuer_role,
        status=issuer_status,
        disabled_at=(FIXED_NOW if issuer_status is MembershipStatus.DISABLED else None),
    )

    db_session.add_all(
        [
            tenant,
            issuer_user,
            issuer_membership,
        ]
    )
    db_session.flush()

    return tenant, issuer_user, issuer_membership


def build_service(
    *,
    audit_recorder: RecordingAuditRecorder | FailingAuditRecorder | None = None,
) -> IssueInvitationService:
    """Build an invitation service with deterministic dependencies."""

    return IssueInvitationService(
        clock=FixedClock(),
        token_factory=fixed_token_factory,
        audit_recorder=audit_recorder,
    )


def assert_safe_invitation_metadata(metadata: JSONObject) -> None:
    """Assert invitation audit metadata excludes secrets and identifiers."""

    forbidden_fragments = (
        "email",
        "token",
        "password",
        "hash",
        "credential",
        "authorization",
        "idempotency_key",
    )

    serialized = str(metadata).lower()

    for fragment in forbidden_fragments:
        assert fragment not in serialized

    assert "idempotency_key" not in metadata


@pytest.mark.parametrize(
    "issuer_role",
    [TenantRole.OWNER, TenantRole.ADMIN],
)
def test_owner_and_admin_can_issue_invitation(
    db_session: Session,
    issuer_role: TenantRole,
) -> None:
    tenant, issuer_user, issuer_membership = create_tenant_with_issuer(
        db_session,
        issuer_role=issuer_role,
    )
    context = audit_context(
        user_id=issuer_user.id,
        role=issuer_role.value,
    )
    recorder = RecordingAuditRecorder()
    service = build_service(audit_recorder=recorder)

    result = service.execute(
        db_session,
        IssueInvitationCommand(
            tenant_id=tenant.id,
            issuer_user_id=issuer_user.id,
            invited_email="  FUTURE.MEMBER@EXAMPLE.COM  ",
            role=TenantRole.STAFF,
            audit_context=context,
        ),
    )

    stored_invitation = db_session.scalar(select(Invitation).where(Invitation.id == result.id))

    assert stored_invitation is not None
    assert result.tenant_id == tenant.id
    assert result.invited_email == "future.member@example.com"
    assert result.role is TenantRole.STAFF
    assert result.expires_at == FIXED_NOW + INVITATION_EXPIRATION
    assert result.token == FIXED_PLAINTEXT_TOKEN
    assert FIXED_PLAINTEXT_TOKEN not in repr(result)

    assert stored_invitation.status is InvitationStatus.PENDING
    assert stored_invitation.created_by_membership_id == (issuer_membership.id)
    assert stored_invitation.created_at == FIXED_NOW
    assert stored_invitation.expires_at == (FIXED_NOW + INVITATION_EXPIRATION)
    assert stored_invitation.token_digest == FIXED_TOKEN.digest
    assert FIXED_PLAINTEXT_TOKEN not in stored_invitation.token_digest

    assert recorder.sessions == [db_session]
    assert len(recorder.commands) == 1

    command = recorder.commands[0]

    assert command.action == AuditAction.INVITATION_CREATED.value
    assert command.resource_type == AuditResourceType.INVITATION.value
    assert command.resource_id == str(result.id)
    assert command.tenant_id == tenant.id
    assert command.actor.actor_type is AuditActorType.USER
    assert command.actor.user_id == issuer_user.id
    assert command.actor.role == issuer_role.value
    assert command.source is AuditSource.HTTP
    assert command.request_id == context.request_id
    assert command.correlation_id == context.correlation_id
    assert command.metadata_version == 1
    assert command.metadata == {
        "invited_role": TenantRole.STAFF.value,
    }
    assert command.idempotency_key == f"invitation-created:{result.id}"
    assert_safe_invitation_metadata(command.metadata)


def test_invitation_rejects_owner_role_before_persistence(
    db_session: Session,
) -> None:
    tenant, issuer_user, _ = create_tenant_with_issuer(db_session)
    recorder = RecordingAuditRecorder()
    service = build_service(audit_recorder=recorder)

    with pytest.raises(InvitationRoleNotAllowedError):
        service.execute(
            db_session,
            IssueInvitationCommand(
                tenant_id=tenant.id,
                issuer_user_id=issuer_user.id,
                invited_email="future.owner@example.com",
                role=TenantRole.OWNER,
                audit_context=audit_context(user_id=issuer_user.id),
            ),
        )

    stored_invitation = db_session.scalar(
        select(Invitation).where(
            Invitation.tenant_id == tenant.id,
            Invitation.invited_email == "future.owner@example.com",
        )
    )

    assert stored_invitation is None
    assert recorder.commands == []


@pytest.mark.parametrize(
    ("issuer_role", "issuer_status"),
    [
        (TenantRole.STAFF, MembershipStatus.ACTIVE),
        (TenantRole.ADMIN, MembershipStatus.DISABLED),
    ],
)
def test_unauthorized_membership_cannot_issue_invitation(
    db_session: Session,
    issuer_role: TenantRole,
    issuer_status: MembershipStatus,
) -> None:
    tenant, issuer_user, _ = create_tenant_with_issuer(
        db_session,
        issuer_role=issuer_role,
        issuer_status=issuer_status,
    )
    recorder = RecordingAuditRecorder()
    service = build_service(audit_recorder=recorder)

    with pytest.raises(InvitationIssuerNotAuthorizedError):
        service.execute(
            db_session,
            IssueInvitationCommand(
                tenant_id=tenant.id,
                issuer_user_id=issuer_user.id,
                invited_email="future.member@example.com",
                role=TenantRole.STAFF,
                audit_context=audit_context(
                    user_id=issuer_user.id,
                    role=issuer_role.value,
                ),
            ),
        )

    assert recorder.commands == []


def test_membership_from_another_tenant_cannot_issue_invitation(
    db_session: Session,
) -> None:
    target_tenant, _, _ = create_tenant_with_issuer(db_session)
    _, external_user, _ = create_tenant_with_issuer(
        db_session,
        issuer_role=TenantRole.ADMIN,
    )
    recorder = RecordingAuditRecorder()
    service = build_service(audit_recorder=recorder)

    with pytest.raises(InvitationIssuerNotAuthorizedError):
        service.execute(
            db_session,
            IssueInvitationCommand(
                tenant_id=target_tenant.id,
                issuer_user_id=external_user.id,
                invited_email="future.member@example.com",
                role=TenantRole.STAFF,
                audit_context=audit_context(user_id=external_user.id),
            ),
        )

    assert recorder.commands == []


@pytest.mark.parametrize(
    "membership_status",
    [
        MembershipStatus.ACTIVE,
        MembershipStatus.DISABLED,
    ],
)
def test_existing_membership_blocks_invitation(
    db_session: Session,
    membership_status: MembershipStatus,
) -> None:
    tenant, issuer_user, _ = create_tenant_with_issuer(db_session)
    invited_email = f"existing-{uuid4()}@example.com"
    invited_user = User(email=invited_email)
    existing_membership = Membership(
        tenant=tenant,
        user=invited_user,
        role=TenantRole.STAFF,
        status=membership_status,
        disabled_at=(FIXED_NOW if membership_status is MembershipStatus.DISABLED else None),
    )
    recorder = RecordingAuditRecorder()

    db_session.add_all([invited_user, existing_membership])
    db_session.flush()

    with pytest.raises(InvitationMembershipAlreadyExistsError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            IssueInvitationCommand(
                tenant_id=tenant.id,
                issuer_user_id=issuer_user.id,
                invited_email=f"  {invited_email.upper()}  ",
                role=TenantRole.ADMIN,
                audit_context=audit_context(user_id=issuer_user.id),
            ),
        )

    assert recorder.commands == []


def test_unexpired_pending_invitation_blocks_reissuance(
    db_session: Session,
) -> None:
    tenant, issuer_user, issuer_membership = create_tenant_with_issuer(db_session)
    invited_email = f"pending-{uuid4()}@example.com"
    pending_invitation = Invitation(
        tenant=tenant,
        invited_email=invited_email,
        role=TenantRole.STAFF,
        token_digest=digest_invitation_token("existing-token"),
        created_by_membership=issuer_membership,
        created_at=FIXED_NOW - timedelta(days=1),
        expires_at=FIXED_NOW + timedelta(days=1),
    )
    recorder = RecordingAuditRecorder()

    db_session.add(pending_invitation)
    db_session.flush()

    with pytest.raises(InvitationAlreadyPendingError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            IssueInvitationCommand(
                tenant_id=tenant.id,
                issuer_user_id=issuer_user.id,
                invited_email=invited_email,
                role=TenantRole.STAFF,
                audit_context=audit_context(user_id=issuer_user.id),
            ),
        )

    assert pending_invitation.status is InvitationStatus.PENDING
    assert recorder.commands == []


def test_expired_pending_invitation_is_replaced(
    db_session: Session,
) -> None:
    tenant, issuer_user, issuer_membership = create_tenant_with_issuer(db_session)
    invited_email = f"expired-{uuid4()}@example.com"
    expired_pending_invitation = Invitation(
        tenant=tenant,
        invited_email=invited_email,
        role=TenantRole.STAFF,
        token_digest=digest_invitation_token("expired-token"),
        created_by_membership=issuer_membership,
        created_at=FIXED_NOW - timedelta(days=8),
        expires_at=FIXED_NOW - timedelta(days=1),
    )
    recorder = RecordingAuditRecorder()

    db_session.add(expired_pending_invitation)
    db_session.flush()

    result = build_service(audit_recorder=recorder).execute(
        db_session,
        IssueInvitationCommand(
            tenant_id=tenant.id,
            issuer_user_id=issuer_user.id,
            invited_email=invited_email,
            role=TenantRole.ADMIN,
            audit_context=audit_context(user_id=issuer_user.id),
        ),
    )

    invitations = list(
        db_session.scalars(
            select(Invitation)
            .where(
                Invitation.tenant_id == tenant.id,
                Invitation.invited_email == invited_email,
            )
            .order_by(Invitation.created_at)
        )
    )

    assert len(invitations) == 2
    assert expired_pending_invitation.status is InvitationStatus.EXPIRED
    assert invitations[-1].id == result.id
    assert invitations[-1].status is InvitationStatus.PENDING
    assert invitations[-1].role is TenantRole.ADMIN
    assert len(recorder.commands) == 1
    assert recorder.commands[0].metadata == {
        "invited_role": TenantRole.ADMIN.value,
    }


def test_disabled_tenant_rejects_invitation(
    db_session: Session,
) -> None:
    tenant, issuer_user, _ = create_tenant_with_issuer(
        db_session,
        tenant_status=TenantStatus.DISABLED,
    )
    recorder = RecordingAuditRecorder()

    with pytest.raises(TenantDisabledError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            IssueInvitationCommand(
                tenant_id=tenant.id,
                issuer_user_id=issuer_user.id,
                invited_email="future.member@example.com",
                role=TenantRole.STAFF,
                audit_context=audit_context(user_id=issuer_user.id),
            ),
        )

    assert recorder.commands == []


def test_missing_tenant_rejects_invitation(
    db_session: Session,
) -> None:
    recorder = RecordingAuditRecorder()
    missing_user_id = uuid4()

    with pytest.raises(TenantNotFoundError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            IssueInvitationCommand(
                tenant_id=uuid4(),
                issuer_user_id=missing_user_id,
                invited_email="future.member@example.com",
                role=TenantRole.STAFF,
                audit_context=audit_context(user_id=missing_user_id),
            ),
        )

    assert recorder.commands == []


def persist_committed_issuer_fixture() -> CommittedIssuerFixture:
    """Persist a tenant issuer for transaction ownership testing."""

    with Session(get_engine()) as session:
        tenant, issuer_user, _ = create_tenant_with_issuer(session)
        fixture = CommittedIssuerFixture(
            tenant_id=tenant.id,
            issuer_user_id=issuer_user.id,
        )
        session.commit()

    return fixture


def delete_committed_issuer_fixture(
    fixture: CommittedIssuerFixture,
) -> None:
    """Delete the committed tenant issuer fixture."""

    with Session(get_engine()) as session:
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == fixture.tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
        session.execute(delete(User).where(User.id == fixture.issuer_user_id))
        session.commit()


def test_issue_invitation_does_not_commit_transaction() -> None:
    fixture = persist_committed_issuer_fixture()
    context = audit_context(user_id=fixture.issuer_user_id)

    try:
        with Session(get_engine()) as session:
            with (
                patch.object(session, "commit", wraps=session.commit) as commit,
                patch.object(session, "rollback", wraps=session.rollback) as rollback,
            ):
                result = build_service().execute(
                    session,
                    IssueInvitationCommand(
                        tenant_id=fixture.tenant_id,
                        issuer_user_id=fixture.issuer_user_id,
                        invited_email="rollback.member@example.com",
                        role=TenantRole.STAFF,
                        audit_context=context,
                    ),
                )
                invitation_id = result.id

            commit.assert_not_called()
            rollback.assert_not_called()
            session.rollback()

        with Session(get_engine()) as verification_session:
            stored_invitation = verification_session.scalar(
                select(Invitation).where(Invitation.id == invitation_id)
            )
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.idempotency_key == (f"invitation-created:{invitation_id}"),
                )
            )

        assert stored_invitation is None
        assert stored_audit is None
    finally:
        delete_committed_issuer_fixture(fixture)


def test_issue_invitation_and_audit_commit_together() -> None:
    fixture = persist_committed_issuer_fixture()
    context = audit_context(user_id=fixture.issuer_user_id)
    invitation_id: UUID | None = None

    try:
        with Session(get_engine()) as session:
            result = build_service().execute(
                session,
                IssueInvitationCommand(
                    tenant_id=fixture.tenant_id,
                    issuer_user_id=fixture.issuer_user_id,
                    invited_email="commit.member@example.com",
                    role=TenantRole.ADMIN,
                    audit_context=context,
                ),
            )
            invitation_id = result.id
            session.commit()

        with Session(get_engine()) as verification_session:
            stored_invitation = verification_session.get(Invitation, invitation_id)
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.idempotency_key == (f"invitation-created:{invitation_id}"),
                )
            )

        assert stored_invitation is not None
        assert stored_invitation.status is InvitationStatus.PENDING
        assert stored_audit is not None
        assert stored_audit.action == AuditAction.INVITATION_CREATED.value
        assert stored_audit.resource_type == AuditResourceType.INVITATION.value
        assert stored_audit.resource_id == str(invitation_id)
        assert stored_audit.tenant_id == fixture.tenant_id
        assert stored_audit.actor_user_id == fixture.issuer_user_id
        assert stored_audit.actor_role == TenantRole.OWNER.value
        assert stored_audit.source == AuditSource.HTTP.value
        assert stored_audit.request_id == context.request_id
        assert stored_audit.correlation_id == context.correlation_id
        assert stored_audit.metadata_version == 1
        assert stored_audit.event_metadata == {
            "invited_role": TenantRole.ADMIN.value,
        }
    finally:
        delete_committed_issuer_fixture(fixture)


def test_audit_failure_prevents_invitation_issue_commit() -> None:
    fixture = persist_committed_issuer_fixture()
    context = audit_context(user_id=fixture.issuer_user_id)
    service = build_service(audit_recorder=FailingAuditRecorder())

    try:
        with Session(get_engine()) as session:
            with (
                patch.object(session, "commit", wraps=session.commit) as commit,
                patch.object(session, "rollback", wraps=session.rollback) as rollback,
                pytest.raises(SimulatedAuditRecordingError),
            ):
                service.execute(
                    session,
                    IssueInvitationCommand(
                        tenant_id=fixture.tenant_id,
                        issuer_user_id=fixture.issuer_user_id,
                        invited_email="audit.fail@example.com",
                        role=TenantRole.STAFF,
                        audit_context=context,
                    ),
                )

            commit.assert_not_called()
            rollback.assert_not_called()
            session.rollback()

        with Session(get_engine()) as verification_session:
            stored_invitation = verification_session.scalar(
                select(Invitation).where(
                    Invitation.tenant_id == fixture.tenant_id,
                    Invitation.invited_email == "audit.fail@example.com",
                )
            )
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.correlation_id == context.correlation_id,
                    AuditLogEntry.action == AuditAction.INVITATION_CREATED.value,
                )
            )

        assert stored_invitation is None
        assert stored_audit is None
    finally:
        delete_committed_issuer_fixture(fixture)


def test_issue_invitation_command_requires_immutable_audit_context() -> None:
    user_id = uuid4()
    context = audit_context(user_id=user_id)
    command = IssueInvitationCommand(
        tenant_id=uuid4(),
        issuer_user_id=user_id,
        invited_email="member@example.com",
        role=TenantRole.STAFF,
        audit_context=context,
    )

    assert command.audit_context is context

    with pytest.raises(FrozenInstanceError):
        command.audit_context = audit_context(user_id=user_id)  # type: ignore[misc]
