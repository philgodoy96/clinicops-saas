from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.invitations.exceptions import (
    InvitationActorNotAuthorizedError,
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
)
from clinicops.invitations.models import Invitation, InvitationStatus
from clinicops.invitations.services.revoke_invitation import (
    RevokeInvitationCommand,
    RevokeInvitationService,
)
from clinicops.invitations.tokens import digest_invitation_token
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

FIXED_NOW = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


@dataclass(frozen=True, slots=True)
class RevocationFixture:
    """Persisted entities required by revocation tests."""

    tenant: Tenant
    actor_user: User
    actor_membership: Membership
    invitation: Invitation


@dataclass(frozen=True, slots=True)
class CommittedRevocationFixture:
    """Identifiers for a committed revocation transaction fixture."""

    tenant_id: UUID
    actor_user_id: UUID
    invitation_id: UUID


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def build_service() -> RevokeInvitationService:
    """Build a revocation service with deterministic time."""

    return RevokeInvitationService(clock=FixedClock())


def create_revocation_fixture(
    session: Session,
    *,
    actor_role: TenantRole = TenantRole.OWNER,
    actor_status: MembershipStatus = MembershipStatus.ACTIVE,
    tenant_status: TenantStatus = TenantStatus.ACTIVE,
    invitation_status: InvitationStatus = InvitationStatus.PENDING,
    expires_at: datetime | None = None,
) -> RevocationFixture:
    """Persist a tenant actor and one invitation."""

    owner_user = User(email=f"owner-{uuid4()}@example.com")
    tenant = Tenant(
        name=f"Northstar Health Clinic {uuid4()}",
        status=tenant_status,
        disabled_at=(FIXED_NOW if tenant_status is TenantStatus.DISABLED else None),
    )
    owner_membership = Membership(
        tenant=tenant,
        user=owner_user,
        role=TenantRole.OWNER,
    )

    if actor_role is TenantRole.OWNER:
        actor_user = owner_user
        actor_membership = owner_membership
    else:
        actor_user = User(email=f"actor-{uuid4()}@example.com")
        actor_membership = Membership(
            tenant=tenant,
            user=actor_user,
            role=actor_role,
            status=actor_status,
            disabled_at=(FIXED_NOW if actor_status is MembershipStatus.DISABLED else None),
        )

    accepted_user: User | None = None
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None

    if invitation_status is InvitationStatus.ACCEPTED:
        accepted_user = User(email=f"accepted-user-{uuid4()}@example.com")
        accepted_at = FIXED_NOW - timedelta(hours=1)

    if invitation_status is InvitationStatus.REVOKED:
        revoked_at = FIXED_NOW - timedelta(hours=1)

    invitation = Invitation(
        tenant=tenant,
        invited_email=f"invitee-{uuid4()}@example.com",
        role=TenantRole.STAFF,
        status=invitation_status,
        token_digest=digest_invitation_token(f"revoke-token-{uuid4()}"),
        created_by_membership=owner_membership,
        accepted_by_user=accepted_user,
        created_at=FIXED_NOW - timedelta(days=1),
        expires_at=(expires_at if expires_at is not None else FIXED_NOW + timedelta(days=6)),
        accepted_at=accepted_at,
        revoked_at=revoked_at,
    )

    entities: list[object] = [
        tenant,
        owner_user,
        owner_membership,
        invitation,
    ]

    if actor_user is not owner_user:
        entities.extend([actor_user, actor_membership])

    if accepted_user is not None:
        entities.append(accepted_user)

    session.add_all(entities)
    session.flush()

    return RevocationFixture(
        tenant=tenant,
        actor_user=actor_user,
        actor_membership=actor_membership,
        invitation=invitation,
    )


@pytest.mark.parametrize(
    "actor_role",
    [TenantRole.OWNER, TenantRole.ADMIN],
)
def test_owner_and_admin_can_revoke_pending_invitation(
    db_session: Session,
    actor_role: TenantRole,
) -> None:
    fixture = create_revocation_fixture(
        db_session,
        actor_role=actor_role,
    )

    result = build_service().execute(
        db_session,
        RevokeInvitationCommand(
            tenant_id=fixture.tenant.id,
            invitation_id=fixture.invitation.id,
            actor_user_id=fixture.actor_user.id,
        ),
    )

    assert result.invitation_id == fixture.invitation.id
    assert result.tenant_id == fixture.tenant.id
    assert result.revoked_at == FIXED_NOW

    assert fixture.invitation.status is InvitationStatus.REVOKED
    assert fixture.invitation.revoked_at == FIXED_NOW
    assert fixture.invitation.accepted_by_user_id is None
    assert fixture.invitation.accepted_at is None


@pytest.mark.parametrize(
    ("actor_role", "actor_status"),
    [
        (TenantRole.STAFF, MembershipStatus.ACTIVE),
        (TenantRole.ADMIN, MembershipStatus.DISABLED),
    ],
)
def test_unauthorized_actor_cannot_revoke_invitation(
    db_session: Session,
    actor_role: TenantRole,
    actor_status: MembershipStatus,
) -> None:
    fixture = create_revocation_fixture(
        db_session,
        actor_role=actor_role,
        actor_status=actor_status,
    )

    with pytest.raises(InvitationActorNotAuthorizedError):
        build_service().execute(
            db_session,
            RevokeInvitationCommand(
                tenant_id=fixture.tenant.id,
                invitation_id=fixture.invitation.id,
                actor_user_id=fixture.actor_user.id,
            ),
        )

    assert fixture.invitation.status is InvitationStatus.PENDING


def test_membership_from_another_tenant_cannot_revoke_invitation(
    db_session: Session,
) -> None:
    target_fixture = create_revocation_fixture(db_session)
    external_fixture = create_revocation_fixture(
        db_session,
        actor_role=TenantRole.ADMIN,
    )

    with pytest.raises(InvitationActorNotAuthorizedError):
        build_service().execute(
            db_session,
            RevokeInvitationCommand(
                tenant_id=target_fixture.tenant.id,
                invitation_id=target_fixture.invitation.id,
                actor_user_id=external_fixture.actor_user.id,
            ),
        )

    assert target_fixture.invitation.status is InvitationStatus.PENDING


def test_invitation_from_another_tenant_is_not_visible(
    db_session: Session,
) -> None:
    actor_fixture = create_revocation_fixture(db_session)
    external_fixture = create_revocation_fixture(db_session)

    with pytest.raises(InvitationNotFoundError):
        build_service().execute(
            db_session,
            RevokeInvitationCommand(
                tenant_id=actor_fixture.tenant.id,
                invitation_id=external_fixture.invitation.id,
                actor_user_id=actor_fixture.actor_user.id,
            ),
        )

    assert external_fixture.invitation.status is InvitationStatus.PENDING


@pytest.mark.parametrize(
    ("invitation_status", "expected_error"),
    [
        (
            InvitationStatus.ACCEPTED,
            InvitationAlreadyAcceptedError,
        ),
        (
            InvitationStatus.REVOKED,
            InvitationRevokedError,
        ),
        (
            InvitationStatus.EXPIRED,
            InvitationExpiredError,
        ),
    ],
)
def test_terminal_invitation_cannot_be_revoked(
    db_session: Session,
    invitation_status: InvitationStatus,
    expected_error: type[Exception],
) -> None:
    fixture = create_revocation_fixture(
        db_session,
        invitation_status=invitation_status,
    )

    with pytest.raises(expected_error):
        build_service().execute(
            db_session,
            RevokeInvitationCommand(
                tenant_id=fixture.tenant.id,
                invitation_id=fixture.invitation.id,
                actor_user_id=fixture.actor_user.id,
            ),
        )


def test_effectively_expired_pending_invitation_cannot_be_revoked(
    db_session: Session,
) -> None:
    fixture = create_revocation_fixture(
        db_session,
        expires_at=FIXED_NOW,
    )

    with pytest.raises(InvitationExpiredError):
        build_service().execute(
            db_session,
            RevokeInvitationCommand(
                tenant_id=fixture.tenant.id,
                invitation_id=fixture.invitation.id,
                actor_user_id=fixture.actor_user.id,
            ),
        )

    assert fixture.invitation.status is InvitationStatus.PENDING
    assert fixture.invitation.revoked_at is None


def test_disabled_tenant_rejects_revocation(
    db_session: Session,
) -> None:
    fixture = create_revocation_fixture(
        db_session,
        tenant_status=TenantStatus.DISABLED,
    )

    with pytest.raises(TenantDisabledError):
        build_service().execute(
            db_session,
            RevokeInvitationCommand(
                tenant_id=fixture.tenant.id,
                invitation_id=fixture.invitation.id,
                actor_user_id=fixture.actor_user.id,
            ),
        )

    assert fixture.invitation.status is InvitationStatus.PENDING


def test_missing_tenant_rejects_revocation(
    db_session: Session,
) -> None:
    with pytest.raises(TenantNotFoundError):
        build_service().execute(
            db_session,
            RevokeInvitationCommand(
                tenant_id=uuid4(),
                invitation_id=uuid4(),
                actor_user_id=uuid4(),
            ),
        )


def test_missing_invitation_rejects_revocation(
    db_session: Session,
) -> None:
    fixture = create_revocation_fixture(db_session)

    with pytest.raises(InvitationNotFoundError):
        build_service().execute(
            db_session,
            RevokeInvitationCommand(
                tenant_id=fixture.tenant.id,
                invitation_id=uuid4(),
                actor_user_id=fixture.actor_user.id,
            ),
        )


def persist_committed_revocation_fixture() -> CommittedRevocationFixture:
    """Persist a revocation fixture for transaction ownership testing."""

    with Session(get_engine()) as session:
        fixture = create_revocation_fixture(session)
        committed_fixture = CommittedRevocationFixture(
            tenant_id=fixture.tenant.id,
            actor_user_id=fixture.actor_user.id,
            invitation_id=fixture.invitation.id,
        )
        session.commit()

    return committed_fixture


def delete_committed_revocation_fixture(
    fixture: CommittedRevocationFixture,
) -> None:
    """Delete a committed revocation fixture."""

    with Session(get_engine()) as session:
        session.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
        session.execute(delete(User).where(User.id == fixture.actor_user_id))
        session.commit()


def test_revoke_invitation_does_not_commit_transaction() -> None:
    fixture = persist_committed_revocation_fixture()

    try:
        with Session(get_engine()) as session:
            build_service().execute(
                session,
                RevokeInvitationCommand(
                    tenant_id=fixture.tenant_id,
                    invitation_id=fixture.invitation_id,
                    actor_user_id=fixture.actor_user_id,
                ),
            )
            session.rollback()

        with Session(get_engine()) as verification_session:
            invitation = verification_session.get(
                Invitation,
                fixture.invitation_id,
            )

        assert invitation is not None
        assert invitation.status is InvitationStatus.PENDING
        assert invitation.revoked_at is None
    finally:
        delete_committed_revocation_fixture(fixture)
