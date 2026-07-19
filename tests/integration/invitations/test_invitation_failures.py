from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.invitations.exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationAlreadyPendingError,
    InvitationRevokedError,
)
from clinicops.invitations.models import Invitation, InvitationStatus
from clinicops.invitations.services.accept_invitation import (
    AcceptInvitationCommand,
    AcceptInvitationService,
)
from clinicops.invitations.services.issue_invitation import (
    IssueInvitationCommand,
    IssueInvitationService,
)
from clinicops.invitations.services.revoke_invitation import (
    RevokeInvitationCommand,
    RevokeInvitationService,
)
from clinicops.invitations.tokens import (
    InvitationToken,
    digest_invitation_token,
)
from clinicops.tenancy.models import Membership, Tenant, TenantRole

FIXED_NOW = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
PENDING_INVITATION_UNIQUE_INDEX = "uq_invitations_one_pending_per_tenant_email"


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


@dataclass(frozen=True, slots=True)
class CommittedTenantFixture:
    """Committed tenant data shared by concurrent sessions."""

    tenant_id: UUID
    owner_user_id: UUID
    owner_membership_id: UUID
    user_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class CommittedInvitationFixture:
    """Committed invitation data shared by concurrent sessions."""

    tenant_id: UUID
    owner_user_id: UUID
    owner_membership_id: UUID
    invited_user_id: UUID
    invitation_id: UUID
    invited_email: str
    token: str
    user_ids: tuple[UUID, ...]


def persist_tenant_fixture() -> CommittedTenantFixture:
    """Persist one tenant with an active owner."""

    with Session(get_engine()) as session:
        owner_user = User(email=f"owner-{uuid4()}@example.com")
        tenant = Tenant(
            name=f"Northstar Health Clinic {uuid4()}",
        )
        owner_membership = Membership(
            tenant=tenant,
            user=owner_user,
            role=TenantRole.OWNER,
        )

        session.add_all(
            [
                owner_user,
                tenant,
                owner_membership,
            ]
        )
        session.flush()

        fixture = CommittedTenantFixture(
            tenant_id=tenant.id,
            owner_user_id=owner_user.id,
            owner_membership_id=owner_membership.id,
            user_ids=(owner_user.id,),
        )
        session.commit()

    return fixture


def persist_invitation_fixture() -> CommittedInvitationFixture:
    """Persist a tenant, invited user, and pending invitation."""

    with Session(get_engine()) as session:
        owner_user = User(email=f"owner-{uuid4()}@example.com")
        invited_email = f"invitee-{uuid4()}@example.com"
        invited_user = User(email=invited_email)
        tenant = Tenant(
            name=f"Northstar Health Clinic {uuid4()}",
        )
        owner_membership = Membership(
            tenant=tenant,
            user=owner_user,
            role=TenantRole.OWNER,
        )
        token = f"concurrent-token-{uuid4()}"
        invitation = Invitation(
            tenant=tenant,
            invited_email=invited_email,
            role=TenantRole.STAFF,
            token_digest=digest_invitation_token(token),
            created_by_membership=owner_membership,
            created_at=FIXED_NOW - timedelta(days=1),
            expires_at=FIXED_NOW + timedelta(days=6),
        )

        session.add_all(
            [
                owner_user,
                invited_user,
                tenant,
                owner_membership,
                invitation,
            ]
        )
        session.flush()

        fixture = CommittedInvitationFixture(
            tenant_id=tenant.id,
            owner_user_id=owner_user.id,
            owner_membership_id=owner_membership.id,
            invited_user_id=invited_user.id,
            invitation_id=invitation.id,
            invited_email=invited_email,
            token=token,
            user_ids=(owner_user.id, invited_user.id),
        )
        session.commit()

    return fixture


def delete_committed_fixture(
    tenant_id: UUID,
    user_ids: tuple[UUID, ...],
) -> None:
    """Delete committed tenancy and identity fixtures."""

    with Session(get_engine()) as session:
        session.execute(delete(Invitation).where(Invitation.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.commit()


def test_concurrent_issuance_creates_one_pending_invitation() -> None:
    fixture = persist_tenant_fixture()
    invited_email = f"concurrent-issue-{uuid4()}@example.com"
    barrier = Barrier(2)

    def issue_invitation(worker_id: int) -> str:
        plaintext_token = f"issue-token-{worker_id}-{uuid4()}"
        token = InvitationToken(
            plaintext=plaintext_token,
            digest=digest_invitation_token(plaintext_token),
        )
        service = IssueInvitationService(
            clock=FixedClock(),
            token_factory=lambda: token,
        )

        with Session(get_engine()) as session:
            barrier.wait(timeout=10)

            try:
                service.execute(
                    session,
                    IssueInvitationCommand(
                        tenant_id=fixture.tenant_id,
                        issuer_user_id=fixture.owner_user_id,
                        invited_email=invited_email,
                        role=TenantRole.STAFF,
                    ),
                )
                session.commit()
                return "issued"
            except InvitationAlreadyPendingError:
                session.rollback()
                return "already_pending"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(issue_invitation, range(2)))

        assert sorted(outcomes) == [
            "already_pending",
            "issued",
        ]

        with Session(get_engine()) as verification_session:
            invitations = list(
                verification_session.scalars(
                    select(Invitation).where(
                        Invitation.tenant_id == fixture.tenant_id,
                        Invitation.invited_email == invited_email,
                    )
                )
            )

        assert len(invitations) == 1
        assert invitations[0].status is InvitationStatus.PENDING
    finally:
        delete_committed_fixture(
            fixture.tenant_id,
            fixture.user_ids,
        )


def test_concurrent_acceptance_creates_exactly_one_membership() -> None:
    fixture = persist_invitation_fixture()
    barrier = Barrier(2)

    def accept_invitation() -> str:
        service = AcceptInvitationService(clock=FixedClock())

        with Session(get_engine()) as session:
            barrier.wait(timeout=10)

            try:
                service.execute(
                    session,
                    AcceptInvitationCommand(token=fixture.token),
                )
                session.commit()
                return "accepted"
            except InvitationAlreadyAcceptedError:
                session.rollback()
                return "already_accepted"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(accept_invitation) for _ in range(2)]
            outcomes = [future.result() for future in futures]

        assert sorted(outcomes) == [
            "accepted",
            "already_accepted",
        ]

        with Session(get_engine()) as verification_session:
            invitation = verification_session.get(
                Invitation,
                fixture.invitation_id,
            )
            membership_count = verification_session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.tenant_id == fixture.tenant_id,
                    Membership.user_id == fixture.invited_user_id,
                )
            )

        assert invitation is not None
        assert invitation.status is InvitationStatus.ACCEPTED
        assert invitation.accepted_by_user_id == fixture.invited_user_id
        assert membership_count == 1
    finally:
        delete_committed_fixture(
            fixture.tenant_id,
            fixture.user_ids,
        )


def test_acceptance_and_revocation_produce_one_terminal_state() -> None:
    fixture = persist_invitation_fixture()
    barrier = Barrier(2)

    def accept_invitation() -> str:
        service = AcceptInvitationService(clock=FixedClock())

        with Session(get_engine()) as session:
            barrier.wait(timeout=10)

            try:
                service.execute(
                    session,
                    AcceptInvitationCommand(token=fixture.token),
                )
                session.commit()
                return "accepted"
            except InvitationRevokedError:
                session.rollback()
                return "revoked_before_acceptance"

    def revoke_invitation() -> str:
        service = RevokeInvitationService(clock=FixedClock())

        with Session(get_engine()) as session:
            barrier.wait(timeout=10)

            try:
                service.execute(
                    session,
                    RevokeInvitationCommand(
                        tenant_id=fixture.tenant_id,
                        invitation_id=fixture.invitation_id,
                        actor_user_id=fixture.owner_user_id,
                    ),
                )
                session.commit()
                return "revoked"
            except InvitationAlreadyAcceptedError:
                session.rollback()
                return "accepted_before_revocation"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            acceptance_future = executor.submit(accept_invitation)
            revocation_future = executor.submit(revoke_invitation)

            acceptance_outcome = acceptance_future.result()
            revocation_outcome = revocation_future.result()

        assert (
            acceptance_outcome,
            revocation_outcome,
        ) in {
            (
                "accepted",
                "accepted_before_revocation",
            ),
            (
                "revoked_before_acceptance",
                "revoked",
            ),
        }

        with Session(get_engine()) as verification_session:
            invitation = verification_session.get(
                Invitation,
                fixture.invitation_id,
            )
            membership_count = verification_session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.tenant_id == fixture.tenant_id,
                    Membership.user_id == fixture.invited_user_id,
                )
            )

        assert invitation is not None

        if invitation.status is InvitationStatus.ACCEPTED:
            assert membership_count == 1
            assert invitation.accepted_by_user_id == fixture.invited_user_id
            assert invitation.accepted_at == FIXED_NOW
            assert invitation.revoked_at is None
        else:
            assert invitation.status is InvitationStatus.REVOKED
            assert membership_count == 0
            assert invitation.accepted_by_user_id is None
            assert invitation.accepted_at is None
            assert invitation.revoked_at == FIXED_NOW
    finally:
        delete_committed_fixture(
            fixture.tenant_id,
            fixture.user_ids,
        )


def test_database_rejects_concurrent_pending_invitations() -> None:
    fixture = persist_tenant_fixture()
    invited_email = f"database-race-{uuid4()}@example.com"
    barrier = Barrier(2)

    def insert_pending_invitation(worker_id: int) -> str:
        token_seed = f"database-token-{worker_id}-{uuid4()}"
        invitation = Invitation(
            tenant_id=fixture.tenant_id,
            invited_email=invited_email,
            role=TenantRole.STAFF,
            token_digest=digest_invitation_token(token_seed),
            created_by_membership_id=fixture.owner_membership_id,
            created_at=FIXED_NOW,
            expires_at=FIXED_NOW + timedelta(days=7),
        )

        with Session(get_engine()) as session:
            session.add(invitation)
            barrier.wait(timeout=10)

            try:
                session.commit()
                return "inserted"
            except IntegrityError as exc:
                session.rollback()

                original_exception = exc.orig

                assert isinstance(
                    original_exception,
                    UniqueViolation,
                )
                assert original_exception.diag.constraint_name == PENDING_INVITATION_UNIQUE_INDEX
                return "unique_violation"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(insert_pending_invitation, range(2)))

        assert sorted(outcomes) == [
            "inserted",
            "unique_violation",
        ]

        with Session(get_engine()) as verification_session:
            invitation_count = verification_session.scalar(
                select(func.count())
                .select_from(Invitation)
                .where(
                    Invitation.tenant_id == fixture.tenant_id,
                    Invitation.invited_email == invited_email,
                    Invitation.status == InvitationStatus.PENDING,
                )
            )

        assert invitation_count == 1
    finally:
        delete_committed_fixture(
            fixture.tenant_id,
            fixture.user_ids,
        )
