from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg.errors import CheckViolation, UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.invitations.models import Invitation, InvitationStatus
from clinicops.invitations.tokens import digest_invitation_token
from clinicops.tenancy.models import Membership, Tenant, TenantRole


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def create_tenant_with_issuer() -> tuple[Tenant, Membership]:
    """Create a tenant with one active owner membership."""

    issuer_user = User(email=f"issuer-{uuid4()}@example.com")
    tenant = Tenant(name=f"Northstar Health Clinic {uuid4()}")
    issuer_membership = Membership(
        tenant=tenant,
        user=issuer_user,
        role=TenantRole.OWNER,
    )

    return tenant, issuer_membership


def create_invitation(
    tenant: Tenant,
    issuer_membership: Membership,
    *,
    invited_email: str | None = None,
    role: TenantRole = TenantRole.STAFF,
    status: InvitationStatus = InvitationStatus.PENDING,
    token_seed: str | None = None,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    accepted_by_user: User | None = None,
    accepted_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> Invitation:
    """Build an invitation with valid defaults for persistence tests."""

    now = created_at or datetime.now(UTC)

    return Invitation(
        tenant=tenant,
        invited_email=(invited_email or f"invitee-{uuid4()}@example.com"),
        role=role,
        status=status,
        token_digest=digest_invitation_token(token_seed or f"token-{uuid4()}"),
        created_by_membership=issuer_membership,
        accepted_by_user=accepted_by_user,
        expires_at=expires_at or now + timedelta(days=7),
        accepted_at=accepted_at,
        revoked_at=revoked_at,
        created_at=created_at,
    )


def assert_unique_violation(
    exception_info: pytest.ExceptionInfo[IntegrityError],
    constraint_name: str,
) -> None:
    """Assert that PostgreSQL rejected a named unique constraint."""

    original_exception = exception_info.value.orig

    assert isinstance(original_exception, UniqueViolation)
    assert original_exception.diag.constraint_name == constraint_name


def assert_check_violation(
    exception_info: pytest.ExceptionInfo[IntegrityError],
    constraint_name: str,
) -> None:
    """Assert that PostgreSQL rejected a named check constraint."""

    original_exception = exception_info.value.orig

    assert isinstance(original_exception, CheckViolation)
    assert original_exception.diag.constraint_name == constraint_name


def test_pending_invitation_persists_with_secure_defaults(
    db_session: Session,
) -> None:
    tenant, issuer_membership = create_tenant_with_issuer()
    invitation = create_invitation(
        tenant,
        issuer_membership,
        invited_email="future.admin@example.com",
        role=TenantRole.ADMIN,
        token_seed="plaintext-token-that-is-not-persisted",
    )

    db_session.add(invitation)
    db_session.flush()

    invitation_id = invitation.id
    db_session.expire_all()

    stored_invitation = db_session.scalar(select(Invitation).where(Invitation.id == invitation_id))

    assert stored_invitation is not None
    assert stored_invitation.tenant_id == tenant.id
    assert stored_invitation.invited_email == "future.admin@example.com"
    assert stored_invitation.role is TenantRole.ADMIN
    assert stored_invitation.status is InvitationStatus.PENDING
    assert stored_invitation.created_by_membership_id == (issuer_membership.id)
    assert stored_invitation.accepted_by_user_id is None
    assert stored_invitation.accepted_at is None
    assert stored_invitation.revoked_at is None
    assert stored_invitation.created_at.tzinfo is not None
    assert stored_invitation.updated_at.tzinfo is not None
    assert stored_invitation.expires_at.tzinfo is not None
    assert stored_invitation.token_digest == digest_invitation_token(
        "plaintext-token-that-is-not-persisted"
    )
    assert "plaintext-token-that-is-not-persisted" not in stored_invitation.token_digest


def test_invitation_rejects_owner_role(
    db_session: Session,
) -> None:
    tenant, issuer_membership = create_tenant_with_issuer()
    invitation = create_invitation(
        tenant,
        issuer_membership,
        role=TenantRole.OWNER,
    )

    db_session.add(invitation)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_check_violation(
        exception_info,
        "ck_invitations_role_not_owner",
    )


def test_invitation_allows_one_pending_record_per_tenant_email(
    db_session: Session,
) -> None:
    tenant, issuer_membership = create_tenant_with_issuer()
    invited_email = f"pending-{uuid4()}@example.com"
    first_invitation = create_invitation(
        tenant,
        issuer_membership,
        invited_email=invited_email,
    )

    db_session.add(first_invitation)
    db_session.flush()

    duplicate_invitation = create_invitation(
        tenant,
        issuer_membership,
        invited_email=invited_email,
    )
    db_session.add(duplicate_invitation)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_unique_violation(
        exception_info,
        "uq_invitations_one_pending_per_tenant_email",
    )


def test_terminal_invitation_allows_new_pending_invitation(
    db_session: Session,
) -> None:
    tenant, issuer_membership = create_tenant_with_issuer()
    invited_email = f"reissued-{uuid4()}@example.com"
    revoked_invitation = create_invitation(
        tenant,
        issuer_membership,
        invited_email=invited_email,
        status=InvitationStatus.REVOKED,
        revoked_at=datetime.now(UTC),
    )
    pending_invitation = create_invitation(
        tenant,
        issuer_membership,
        invited_email=invited_email,
    )

    db_session.add_all(
        [
            revoked_invitation,
            pending_invitation,
        ]
    )
    db_session.flush()

    assert revoked_invitation.status is InvitationStatus.REVOKED
    assert pending_invitation.status is InvitationStatus.PENDING


def test_invitation_token_digest_is_globally_unique(
    db_session: Session,
) -> None:
    tenant, issuer_membership = create_tenant_with_issuer()
    token_seed = f"shared-token-{uuid4()}"
    first_invitation = create_invitation(
        tenant,
        issuer_membership,
        token_seed=token_seed,
    )

    db_session.add(first_invitation)
    db_session.flush()

    duplicate_digest_invitation = create_invitation(
        tenant,
        issuer_membership,
        token_seed=token_seed,
    )
    db_session.add(duplicate_digest_invitation)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_unique_violation(
        exception_info,
        "uq_invitations_token_digest",
    )


def test_invitation_expiration_must_follow_creation(
    db_session: Session,
) -> None:
    tenant, issuer_membership = create_tenant_with_issuer()
    created_at = datetime.now(UTC)
    invitation = create_invitation(
        tenant,
        issuer_membership,
        created_at=created_at,
        expires_at=created_at,
    )

    db_session.add(invitation)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_check_violation(
        exception_info,
        "ck_invitations_expires_after_created_at",
    )


@pytest.mark.parametrize(
    ("status", "constraint_name"),
    [
        (
            InvitationStatus.ACCEPTED,
            "ck_invitations_accepted_state",
        ),
        (
            InvitationStatus.REVOKED,
            "ck_invitations_revoked_state",
        ),
    ],
)
def test_terminal_invitation_requires_consistent_state(
    db_session: Session,
    status: InvitationStatus,
    constraint_name: str,
) -> None:
    tenant, issuer_membership = create_tenant_with_issuer()
    invitation = create_invitation(
        tenant,
        issuer_membership,
        status=status,
    )

    db_session.add(invitation)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_check_violation(exception_info, constraint_name)
