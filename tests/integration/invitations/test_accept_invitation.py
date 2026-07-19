from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

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


class FailingMembershipTenantRepository(TenantRepository):
    """Flush acceptance state and then simulate a later failure."""

    def add_membership_and_flush(
        self,
        session: Session,
        membership: Membership,
    ) -> None:
        super().add_membership_and_flush(session, membership)
        raise SimulatedAcceptanceFailure


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
) -> AcceptInvitationService:
    """Build an acceptance service with deterministic time."""

    return AcceptInvitationService(
        tenant_repository=tenant_repository,
        clock=FixedClock(),
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

    command = AcceptInvitationCommand(token=token)
    result = build_service().execute(db_session, command)

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


def test_new_user_is_created_with_password_credential(
    db_session: Session,
) -> None:
    invited_email = f"new-user-{uuid4()}@example.com"
    tenant, invitation, token, _ = create_pending_invitation(
        db_session,
        invited_email=invited_email,
    )
    plaintext_password = "Correct horse battery staple"

    result = build_service().execute(
        db_session,
        AcceptInvitationCommand(
            token=token,
            password=plaintext_password,
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


def test_new_user_requires_password(
    db_session: Session,
) -> None:
    _, invitation, token, _ = create_pending_invitation(db_session)

    with pytest.raises(InvitationPasswordRequiredError):
        build_service().execute(
            db_session,
            AcceptInvitationCommand(token=token),
        )

    assert invitation.status is InvitationStatus.PENDING
    assert invitation.accepted_by_user_id is None


def test_unknown_token_is_rejected(
    db_session: Session,
) -> None:
    with pytest.raises(InvitationTokenInvalidError):
        build_service().execute(
            db_session,
            AcceptInvitationCommand(token="unknown-token"),
        )


def test_expired_invitation_is_rejected(
    db_session: Session,
) -> None:
    _, invitation, token, _ = create_pending_invitation(
        db_session,
        created_at=FIXED_NOW - timedelta(days=8),
        expires_at=FIXED_NOW - timedelta(seconds=1),
    )

    with pytest.raises(InvitationExpiredError):
        build_service().execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                password="Correct horse battery staple",
            ),
        )

    assert invitation.status is InvitationStatus.PENDING


def test_revoked_invitation_is_rejected(
    db_session: Session,
) -> None:
    _, invitation, token, _ = create_pending_invitation(db_session)
    invitation.status = InvitationStatus.REVOKED
    invitation.revoked_at = FIXED_NOW - timedelta(hours=1)
    db_session.flush()

    with pytest.raises(InvitationRevokedError):
        build_service().execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                password="Correct horse battery staple",
            ),
        )


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

    service = build_service()
    command = AcceptInvitationCommand(token=token)

    service.execute(db_session, command)

    with pytest.raises(InvitationAlreadyAcceptedError):
        service.execute(db_session, command)

    assert invitation.status is InvitationStatus.ACCEPTED


def test_disabled_tenant_rejects_acceptance(
    db_session: Session,
) -> None:
    _, invitation, token, _ = create_pending_invitation(
        db_session,
        tenant_status=TenantStatus.DISABLED,
    )

    with pytest.raises(TenantDisabledError):
        build_service().execute(
            db_session,
            AcceptInvitationCommand(
                token=token,
                password="Correct horse battery staple",
            ),
        )

    assert invitation.status is InvitationStatus.PENDING


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

    with pytest.raises(UserDisabledError):
        build_service().execute(
            db_session,
            AcceptInvitationCommand(token=token),
        )

    assert invitation.status is InvitationStatus.PENDING


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

    with pytest.raises(InvitationMembershipAlreadyExistsError):
        build_service().execute(
            db_session,
            AcceptInvitationCommand(token=token),
        )

    assert invitation.status is InvitationStatus.PENDING


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
        session.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.commit()


def test_successful_acceptance_does_not_commit_transaction() -> None:
    fixture = persist_committed_invitation_fixture(
        include_invited_user=True,
    )

    assert fixture.invited_user_id is not None

    try:
        with Session(get_engine()) as session:
            build_service().execute(
                session,
                AcceptInvitationCommand(token=fixture.token),
            )
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

        assert invitation is not None
        assert invitation.status is InvitationStatus.PENDING
        assert invitation.accepted_by_user_id is None
        assert membership is None
    finally:
        delete_committed_invitation_fixture(fixture)


def test_failure_after_new_user_creation_rolls_back_everything() -> None:
    fixture = persist_committed_invitation_fixture(
        include_invited_user=False,
    )
    service = build_service(
        tenant_repository=FailingMembershipTenantRepository(),
    )

    try:
        with Session(get_engine()) as session:
            with pytest.raises(SimulatedAcceptanceFailure):
                service.execute(
                    session,
                    AcceptInvitationCommand(
                        token=fixture.token,
                        password="Correct horse battery staple",
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
    finally:
        delete_committed_invitation_fixture(fixture)
