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
    AuditActor,
    JSONObject,
    RecordAuditLogCommand,
    RecordedAuditLog,
)
from clinicops.audit.enums import AuditActorType, AuditSource
from clinicops.audit.models import AuditLogEntry
from clinicops.db.session import get_engine
from clinicops.identity.exceptions import UserDisabledError
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import Argon2PasswordHasher
from clinicops.invitations.exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationMembershipAlreadyExistsError,
    InvitationPasswordRequiredError,
    InvitationRevokedError,
    InvitationTokenInvalidError,
)
from clinicops.invitations.models import Invitation, InvitationStatus
from clinicops.invitations.services.accept_invitation import (
    AcceptInvitationCommand,
    AcceptInvitationService,
)
from clinicops.invitations.tokens import digest_invitation_token
from clinicops.tenancy.exceptions import TenantDisabledError
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.repository import TenantRepository

FIXED_NOW = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


class SimulatedAcceptanceFailure(RuntimeError):
    """Represent a failure after acceptance state has been flushed."""


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class FailingMembershipTenantRepository(TenantRepository):
    """Flush acceptance state and then simulate a later failure."""

    def add_membership_and_flush(
        self,
        session: Session,
        membership: Membership,
    ) -> None:
        super().add_membership_and_flush(session, membership)
        raise SimulatedAcceptanceFailure


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
    """Raise after acceptance state has already been flushed."""

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError


@dataclass(frozen=True, slots=True)
class CommittedInvitationFixture:
    """Identifiers for a committed invitation acceptance fixture."""

    tenant_id: UUID
    invitation_id: UUID
    issuer_user_id: UUID
    invited_email: str
    token: str
    invited_user_id: UUID | None = None


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def system_audit_context() -> AuditRecordingContext:
    """Build one immutable system HTTP audit context."""

    return AuditRecordingContext(
        actor=AuditActor.system(),
        source=AuditSource.HTTP,
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
    )


def assert_safe_acceptance_metadata(metadata: JSONObject) -> None:
    """Assert acceptance audit metadata excludes secrets."""

    serialized = str(metadata).lower()

    for fragment in (
        "email",
        "token",
        "password",
        "hash",
        "credential",
        "authorization",
        "membership_id",
        "user_was_created",
        "idempotency_key",
    ):
        assert fragment not in serialized

    assert "idempotency_key" not in metadata


def create_pending_invitation(
    session: Session,
    *,
    invited_email: str | None = None,
    tenant_status: TenantStatus = TenantStatus.ACTIVE,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    role: TenantRole = TenantRole.STAFF,
) -> tuple[Tenant, Invitation, str, User]:
    """Persist a tenant owner and one pending invitation."""

    token = f"accept-token-{uuid4()}"
    issuer_user = User(email=f"issuer-{uuid4()}@example.com")
    tenant = Tenant(
        name=f"Northstar Health Clinic {uuid4()}",
        status=tenant_status,
        disabled_at=(FIXED_NOW if tenant_status is TenantStatus.DISABLED else None),
    )
    issuer_membership = Membership(
        tenant=tenant,
        user=issuer_user,
        role=TenantRole.OWNER,
    )
    invitation_created_at = created_at if created_at is not None else FIXED_NOW - timedelta(days=1)
    invitation = Invitation(
        tenant=tenant,
        invited_email=(
            invited_email if invited_email is not None else f"invitee-{uuid4()}@example.com"
        ),
        role=role,
        token_digest=digest_invitation_token(token),
        created_by_membership=issuer_membership,
        created_at=invitation_created_at,
        expires_at=(expires_at if expires_at is not None else FIXED_NOW + timedelta(days=6)),
    )

    session.add_all(
        [
            tenant,
            issuer_user,
            issuer_membership,
            invitation,
        ]
    )
    session.flush()

    return tenant, invitation, token, issuer_user


def build_service(
    *,
    tenant_repository: TenantRepository | None = None,
    audit_recorder: RecordingAuditRecorder | FailingAuditRecorder | None = None,
) -> AcceptInvitationService:
    """Build an acceptance service with deterministic time."""

    return AcceptInvitationService(
        tenant_repository=tenant_repository,
        clock=FixedClock(),
        audit_recorder=audit_recorder,
    )


def test_existing_active_user_accepts_without_password(
    db_session: Session,
) -> None:
    invited_email = f"existing-{uuid4()}@example.com"
    tenant, invitation, token, _ = create_pending_invitation(
        db_session,
        invited_email=invited_email,
        role=TenantRole.ADMIN,
    )
    invited_user = User(email=invited_email)
    db_session.add(invited_user)
    db_session.flush()

    context = system_audit_context()
    recorder = RecordingAuditRecorder()
    command = AcceptInvitationCommand(
        token=token,
        audit_context=context,
    )
    result = build_service(audit_recorder=recorder).execute(db_session, command)

    stored_membership = db_session.scalar(
        select(Membership).where(Membership.id == result.membership_id)
    )

    assert result.invitation_id == invitation.id
    assert result.tenant_id == tenant.id
    assert result.user_id == invited_user.id
    assert result.role is TenantRole.ADMIN
    assert result.user_was_created is False
    assert result.accepted_at == FIXED_NOW
    assert token not in repr(command)

    assert stored_membership is not None
    assert stored_membership.user_id == invited_user.id
    assert stored_membership.tenant_id == tenant.id
    assert stored_membership.role is TenantRole.ADMIN
    assert stored_membership.status is MembershipStatus.ACTIVE

    assert invitation.status is InvitationStatus.ACCEPTED
    assert invitation.accepted_by_user_id == invited_user.id
    assert invitation.accepted_at == FIXED_NOW

    assert recorder.sessions == [db_session]
    assert len(recorder.commands) == 1

    audit_command = recorder.commands[0]

    assert audit_command.action == AuditAction.INVITATION_ACCEPTED.value
    assert audit_command.resource_type == AuditResourceType.INVITATION.value
    assert audit_command.resource_id == str(invitation.id)
    assert audit_command.tenant_id == tenant.id
    assert audit_command.actor.actor_type is AuditActorType.SYSTEM
    assert audit_command.actor.user_id is None
    assert audit_command.actor.role is None
    assert audit_command.source is AuditSource.HTTP
    assert audit_command.request_id == context.request_id
    assert audit_command.correlation_id == context.correlation_id
    assert audit_command.metadata_version == 1
    assert audit_command.metadata == {
        "accepted_user_id": str(invited_user.id),
        "accepted_role": TenantRole.ADMIN.value,
    }
    assert audit_command.idempotency_key == f"invitation-accepted:{invitation.id}"
    assert_safe_acceptance_metadata(audit_command.metadata)


def test_new_user_is_created_with_password_credential(
    db_session: Session,
) -> None:
    invited_email = f"new-user-{uuid4()}@example.com"
    tenant, invitation, token, _ = create_pending_invitation(
        db_session,
        invited_email=invited_email,
    )
    plaintext_password = "Correct horse battery staple"
    recorder = RecordingAuditRecorder()

    result = build_service(audit_recorder=recorder).execute(
        db_session,
        AcceptInvitationCommand(
            token=token,
            password=plaintext_password,
            audit_context=system_audit_context(),
        ),
    )

    stored_user = db_session.scalar(select(User).where(User.id == result.user_id))
    stored_membership = db_session.scalar(
        select(Membership).where(Membership.id == result.membership_id)
    )

    assert result.user_was_created is True
    assert result.tenant_id == tenant.id
    assert result.role is TenantRole.STAFF

    assert stored_user is not None
    assert stored_user.email == invited_email
    assert stored_user.status is UserStatus.ACTIVE

    stored_credential = stored_user.password_credential

    assert stored_credential is not None
    assert plaintext_password not in stored_credential.password_hash
    assert (
        Argon2PasswordHasher().verify(
            plaintext_password,
            stored_credential.password_hash,
        )
        is True
    )

    assert stored_membership is not None
    assert stored_membership.user_id == stored_user.id
    assert stored_membership.tenant_id == tenant.id
    assert invitation.status is InvitationStatus.ACCEPTED
    assert invitation.accepted_by_user_id == stored_user.id
    assert len(recorder.commands) == 1
    assert recorder.commands[0].metadata == {
        "accepted_user_id": str(stored_user.id),
        "accepted_role": TenantRole.STAFF.value,
    }
    assert_safe_acceptance_metadata(recorder.commands[0].metadata)


def test_new_user_requires_password(
    db_session: Session,
) -> None:
    _, invitation, token, _ = create_pending_invitation(db_session)
    recorder = RecordingAuditRecorder()

    with pytest.raises(InvitationPasswordRequiredError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                audit_context=system_audit_context(),
            ),
        )

    assert invitation.status is InvitationStatus.PENDING
    assert invitation.accepted_by_user_id is None
    assert recorder.commands == []


def test_unknown_token_is_rejected(
    db_session: Session,
) -> None:
    recorder = RecordingAuditRecorder()

    with pytest.raises(InvitationTokenInvalidError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            AcceptInvitationCommand(
                token="unknown-token",
                audit_context=system_audit_context(),
            ),
        )

    assert recorder.commands == []


def test_expired_invitation_is_rejected(
    db_session: Session,
) -> None:
    _, invitation, token, _ = create_pending_invitation(
        db_session,
        created_at=FIXED_NOW - timedelta(days=8),
        expires_at=FIXED_NOW - timedelta(seconds=1),
    )
    recorder = RecordingAuditRecorder()

    with pytest.raises(InvitationExpiredError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                password="Correct horse battery staple",
                audit_context=system_audit_context(),
            ),
        )

    assert invitation.status is InvitationStatus.PENDING
    assert recorder.commands == []


def test_revoked_invitation_is_rejected(
    db_session: Session,
) -> None:
    _, invitation, token, _ = create_pending_invitation(db_session)
    invitation.status = InvitationStatus.REVOKED
    invitation.revoked_at = FIXED_NOW - timedelta(hours=1)
    db_session.flush()
    recorder = RecordingAuditRecorder()

    with pytest.raises(InvitationRevokedError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                password="Correct horse battery staple",
                audit_context=system_audit_context(),
            ),
        )

    assert recorder.commands == []


def test_accepted_invitation_cannot_be_replayed(
    db_session: Session,
) -> None:
    invited_email = f"replay-{uuid4()}@example.com"
    _, invitation, token, _ = create_pending_invitation(
        db_session,
        invited_email=invited_email,
    )
    invited_user = User(email=invited_email)
    db_session.add(invited_user)
    db_session.flush()

    recorder = RecordingAuditRecorder()
    service = build_service(audit_recorder=recorder)
    command = AcceptInvitationCommand(
        token=token,
        audit_context=system_audit_context(),
    )

    service.execute(db_session, command)

    with pytest.raises(InvitationAlreadyAcceptedError):
        service.execute(db_session, command)

    assert invitation.status is InvitationStatus.ACCEPTED
    assert len(recorder.commands) == 1


def test_disabled_tenant_rejects_acceptance(
    db_session: Session,
) -> None:
    _, invitation, token, _ = create_pending_invitation(
        db_session,
        tenant_status=TenantStatus.DISABLED,
    )
    recorder = RecordingAuditRecorder()

    with pytest.raises(TenantDisabledError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                password="Correct horse battery staple",
                audit_context=system_audit_context(),
            ),
        )

    assert invitation.status is InvitationStatus.PENDING
    assert recorder.commands == []


def test_disabled_existing_user_rejects_acceptance(
    db_session: Session,
) -> None:
    invited_email = f"disabled-{uuid4()}@example.com"
    _, invitation, token, _ = create_pending_invitation(
        db_session,
        invited_email=invited_email,
    )
    invited_user = User(
        email=invited_email,
        status=UserStatus.DISABLED,
        disabled_at=FIXED_NOW,
    )
    db_session.add(invited_user)
    db_session.flush()
    recorder = RecordingAuditRecorder()

    with pytest.raises(UserDisabledError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                audit_context=system_audit_context(),
            ),
        )

    assert invitation.status is InvitationStatus.PENDING
    assert recorder.commands == []


def test_existing_membership_rejects_acceptance(
    db_session: Session,
) -> None:
    invited_email = f"member-{uuid4()}@example.com"
    tenant, invitation, token, _ = create_pending_invitation(
        db_session,
        invited_email=invited_email,
    )
    invited_user = User(email=invited_email)
    existing_membership = Membership(
        tenant=tenant,
        user=invited_user,
        role=TenantRole.STAFF,
    )
    db_session.add_all([invited_user, existing_membership])
    db_session.flush()
    recorder = RecordingAuditRecorder()

    with pytest.raises(InvitationMembershipAlreadyExistsError):
        build_service(audit_recorder=recorder).execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                audit_context=system_audit_context(),
            ),
        )

    assert invitation.status is InvitationStatus.PENDING
    assert recorder.commands == []


def persist_committed_invitation_fixture(
    *,
    include_invited_user: bool,
) -> CommittedInvitationFixture:
    """Persist an invitation fixture for transaction boundary tests."""

    with Session(get_engine()) as session:
        invited_email = f"committed-{uuid4()}@example.com"
        tenant, invitation, token, issuer_user = create_pending_invitation(
            session,
            invited_email=invited_email,
        )
        invited_user_id: UUID | None = None

        if include_invited_user:
            invited_user = User(email=invited_email)
            session.add(invited_user)
            session.flush()
            invited_user_id = invited_user.id

        fixture = CommittedInvitationFixture(
            tenant_id=tenant.id,
            invitation_id=invitation.id,
            issuer_user_id=issuer_user.id,
            invited_email=invited_email,
            token=token,
            invited_user_id=invited_user_id,
        )
        session.commit()

    return fixture


def delete_committed_invitation_fixture(
    fixture: CommittedInvitationFixture,
) -> None:
    """Delete a committed invitation fixture and its global users."""

    user_ids = [fixture.issuer_user_id]

    if fixture.invited_user_id is not None:
        user_ids.append(fixture.invited_user_id)

    with Session(get_engine()) as session:
        accepted_user_ids = list(
            session.scalars(
                select(Membership.user_id).where(Membership.tenant_id == fixture.tenant_id)
            )
        )
        user_ids.extend(accepted_user_ids)
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == fixture.tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.commit()


def test_successful_acceptance_does_not_commit_transaction() -> None:
    fixture = persist_committed_invitation_fixture(
        include_invited_user=True,
    )
    context = system_audit_context()

    assert fixture.invited_user_id is not None

    try:
        with Session(get_engine()) as session:
            with (
                patch.object(session, "commit", wraps=session.commit) as commit,
                patch.object(session, "rollback", wraps=session.rollback) as rollback,
            ):
                build_service().execute(
                    session,
                    AcceptInvitationCommand(
                        token=fixture.token,
                        audit_context=context,
                    ),
                )

            commit.assert_not_called()
            rollback.assert_not_called()
            session.rollback()

        with Session(get_engine()) as verification_session:
            invitation = verification_session.get(
                Invitation,
                fixture.invitation_id,
            )
            membership = verification_session.scalar(
                select(Membership).where(
                    Membership.tenant_id == fixture.tenant_id,
                    Membership.user_id == fixture.invited_user_id,
                )
            )
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.idempotency_key
                    == (f"invitation-accepted:{fixture.invitation_id}"),
                )
            )

        assert invitation is not None
        assert invitation.status is InvitationStatus.PENDING
        assert invitation.accepted_by_user_id is None
        assert membership is None
        assert stored_audit is None
    finally:
        delete_committed_invitation_fixture(fixture)


def test_failure_after_new_user_creation_rolls_back_everything() -> None:
    fixture = persist_committed_invitation_fixture(
        include_invited_user=False,
    )
    recorder = RecordingAuditRecorder()
    service = build_service(
        tenant_repository=FailingMembershipTenantRepository(),
        audit_recorder=recorder,
    )

    try:
        with Session(get_engine()) as session:
            with pytest.raises(SimulatedAcceptanceFailure):
                service.execute(
                    session,
                    AcceptInvitationCommand(
                        token=fixture.token,
                        password="Correct horse battery staple",
                        audit_context=system_audit_context(),
                    ),
                )

            session.rollback()

        with Session(get_engine()) as verification_session:
            invitation = verification_session.get(
                Invitation,
                fixture.invitation_id,
            )
            invited_user = verification_session.scalar(
                select(User).where(User.email == fixture.invited_email)
            )
            membership = verification_session.scalar(
                select(Membership)
                .join(User, Membership.user_id == User.id)
                .where(
                    Membership.tenant_id == fixture.tenant_id,
                    User.email == fixture.invited_email,
                )
            )
            credential = verification_session.scalar(
                select(PasswordCredential)
                .join(User, PasswordCredential.user_id == User.id)
                .where(User.email == fixture.invited_email)
            )

        assert invitation is not None
        assert invitation.status is InvitationStatus.PENDING
        assert invitation.accepted_by_user_id is None
        assert invitation.accepted_at is None
        assert invited_user is None
        assert membership is None
        assert credential is None
        assert recorder.commands == []
    finally:
        delete_committed_invitation_fixture(fixture)


def test_acceptance_and_audit_commit_together() -> None:
    fixture = persist_committed_invitation_fixture(
        include_invited_user=True,
    )
    context = system_audit_context()

    assert fixture.invited_user_id is not None

    try:
        with Session(get_engine()) as session:
            build_service().execute(
                session,
                AcceptInvitationCommand(
                    token=fixture.token,
                    audit_context=context,
                ),
            )
            session.commit()

        with Session(get_engine()) as verification_session:
            invitation = verification_session.get(
                Invitation,
                fixture.invitation_id,
            )
            membership = verification_session.scalar(
                select(Membership).where(
                    Membership.tenant_id == fixture.tenant_id,
                    Membership.user_id == fixture.invited_user_id,
                )
            )
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.idempotency_key
                    == (f"invitation-accepted:{fixture.invitation_id}"),
                )
            )

        assert invitation is not None
        assert invitation.status is InvitationStatus.ACCEPTED
        assert membership is not None
        assert stored_audit is not None
        assert stored_audit.action == AuditAction.INVITATION_ACCEPTED.value
        assert stored_audit.resource_id == str(fixture.invitation_id)
        assert stored_audit.actor_type == AuditActorType.SYSTEM.value
        assert stored_audit.actor_user_id is None
        assert stored_audit.actor_role is None
        assert stored_audit.source == AuditSource.HTTP.value
        assert stored_audit.request_id == context.request_id
        assert stored_audit.correlation_id == context.correlation_id
        assert stored_audit.event_metadata == {
            "accepted_user_id": str(fixture.invited_user_id),
            "accepted_role": TenantRole.STAFF.value,
        }
    finally:
        delete_committed_invitation_fixture(fixture)


def test_audit_failure_prevents_acceptance_commit() -> None:
    fixture = persist_committed_invitation_fixture(
        include_invited_user=True,
    )
    context = system_audit_context()
    service = build_service(audit_recorder=FailingAuditRecorder())

    assert fixture.invited_user_id is not None

    try:
        with Session(get_engine()) as session:
            with (
                patch.object(session, "commit", wraps=session.commit) as commit,
                patch.object(session, "rollback", wraps=session.rollback) as rollback,
                pytest.raises(SimulatedAuditRecordingError),
            ):
                service.execute(
                    session,
                    AcceptInvitationCommand(
                        token=fixture.token,
                        audit_context=context,
                    ),
                )

            commit.assert_not_called()
            rollback.assert_not_called()
            session.rollback()

        with Session(get_engine()) as verification_session:
            invitation = verification_session.get(
                Invitation,
                fixture.invitation_id,
            )
            membership = verification_session.scalar(
                select(Membership).where(
                    Membership.tenant_id == fixture.tenant_id,
                    Membership.user_id == fixture.invited_user_id,
                )
            )
            stored_audit = verification_session.scalar(
                select(AuditLogEntry).where(
                    AuditLogEntry.idempotency_key
                    == (f"invitation-accepted:{fixture.invitation_id}"),
                )
            )

        assert invitation is not None
        assert invitation.status is InvitationStatus.PENDING
        assert membership is None
        assert stored_audit is None
    finally:
        delete_committed_invitation_fixture(fixture)


def test_accept_invitation_command_requires_immutable_audit_context() -> None:
    context = system_audit_context()
    command = AcceptInvitationCommand(
        token="opaque-token",
        audit_context=context,
    )

    assert command.audit_context is context

    with pytest.raises(FrozenInstanceError):
        command.audit_context = system_audit_context()  # type: ignore[misc]
