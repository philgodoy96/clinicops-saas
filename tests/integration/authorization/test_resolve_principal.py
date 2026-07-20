from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.authentication.access_tokens import (
    AccessTokenCodec,
    IssuedAccessToken,
)
from clinicops.authentication.config import (
    AuthenticationTokenConfig,
)
from clinicops.authentication.exceptions import (
    AccessTokenExpiredError,
    AuthenticationSessionExpiredError,
    AuthenticationSessionInactiveError,
    AuthenticationSessionNotFoundError,
)
from clinicops.authentication.models import (
    AuthSession,
    AuthSessionStatus,
)
from clinicops.authentication.services.resolve_principal import (
    ResolveAuthenticatedPrincipalCommand,
    ResolveAuthenticatedPrincipalService,
)
from clinicops.db.session import get_engine
from clinicops.identity.exceptions import UserDisabledError
from clinicops.identity.models import User, UserStatus

FIXED_NOW = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
TOKEN_ISSUED_AT = FIXED_NOW - timedelta(minutes=5)
SESSION_CREATED_AT = FIXED_NOW - timedelta(days=1)
SESSION_EXPIRES_AT = FIXED_NOW + timedelta(days=29)
SIGNING_KEY = "development-signing-key-with-32-bytes-minimum"


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def build_access_token_codec() -> AccessTokenCodec:
    """Build deterministic access-token configuration."""

    return AccessTokenCodec(
        AuthenticationTokenConfig(
            issuer="clinicops",
            audience="clinicops-api",
            signing_key=SIGNING_KEY,
        )
    )


def build_service() -> ResolveAuthenticatedPrincipalService:
    """Build principal resolution with deterministic time."""

    return ResolveAuthenticatedPrincipalService(
        access_token_codec=build_access_token_codec(),
        clock=FixedClock(),
    )


def create_user(
    session: Session,
    *,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    """Persist one global user."""

    user = User(
        email=f"user-{uuid4()}@example.com",
        status=status,
        disabled_at=(FIXED_NOW if status is UserStatus.DISABLED else None),
    )
    session.add(user)
    session.flush()

    return user


def create_authentication_session(
    session: Session,
    *,
    user: User,
    status: AuthSessionStatus = AuthSessionStatus.ACTIVE,
    expires_at: datetime = SESSION_EXPIRES_AT,
) -> AuthSession:
    """Persist one authentication session."""

    auth_session = AuthSession(
        user=user,
        status=status,
        expires_at=expires_at,
        last_rotated_at=SESSION_CREATED_AT,
        revoked_at=(
            FIXED_NOW - timedelta(hours=1) if status is AuthSessionStatus.REVOKED else None
        ),
        compromised_at=(
            FIXED_NOW - timedelta(hours=1) if status is AuthSessionStatus.COMPROMISED else None
        ),
        created_at=SESSION_CREATED_AT,
    )
    session.add(auth_session)
    session.flush()

    return auth_session


def issue_access_token(
    *,
    user_id: UUID,
    session_id: UUID,
    issued_at: datetime = TOKEN_ISSUED_AT,
) -> IssuedAccessToken:
    """Issue an access token for deterministic test claims."""

    return build_access_token_codec().issue(
        user_id=user_id,
        session_id=session_id,
        issued_at=issued_at,
    )


def test_active_user_and_session_resolve_authenticated_principal(
    db_session: Session,
) -> None:
    user = create_user(db_session)
    auth_session = create_authentication_session(
        db_session,
        user=user,
    )
    issued_token = issue_access_token(
        user_id=user.id,
        session_id=auth_session.id,
    )
    command = ResolveAuthenticatedPrincipalCommand(access_token=issued_token.token)

    principal = build_service().execute(db_session, command)

    decoded_claims = build_access_token_codec().decode(
        issued_token.token,
        now=FIXED_NOW,
    )

    assert principal.user_id == user.id
    assert principal.session_id == auth_session.id
    assert principal.access_token_id == decoded_claims.token_id
    assert principal.authenticated_at == TOKEN_ISSUED_AT
    assert principal.access_token_expires_at == issued_token.expires_at
    assert principal.session_expires_at == SESSION_EXPIRES_AT
    assert issued_token.token not in repr(command)


def test_missing_session_cannot_resolve_principal(
    db_session: Session,
) -> None:
    user = create_user(db_session)
    issued_token = issue_access_token(
        user_id=user.id,
        session_id=uuid4(),
    )

    with pytest.raises(AuthenticationSessionNotFoundError):
        build_service().execute(
            db_session,
            ResolveAuthenticatedPrincipalCommand(access_token=issued_token.token),
        )


def test_session_subject_mismatch_cannot_resolve_principal(
    db_session: Session,
) -> None:
    token_user = create_user(db_session)
    session_owner = create_user(db_session)
    auth_session = create_authentication_session(
        db_session,
        user=session_owner,
    )
    issued_token = issue_access_token(
        user_id=token_user.id,
        session_id=auth_session.id,
    )

    with pytest.raises(AuthenticationSessionNotFoundError):
        build_service().execute(
            db_session,
            ResolveAuthenticatedPrincipalCommand(access_token=issued_token.token),
        )


@pytest.mark.parametrize(
    "session_status",
    [
        AuthSessionStatus.REVOKED,
        AuthSessionStatus.COMPROMISED,
    ],
)
def test_inactive_session_cannot_resolve_principal(
    db_session: Session,
    session_status: AuthSessionStatus,
) -> None:
    user = create_user(db_session)
    auth_session = create_authentication_session(
        db_session,
        user=user,
        status=session_status,
    )
    issued_token = issue_access_token(
        user_id=user.id,
        session_id=auth_session.id,
    )

    with pytest.raises(AuthenticationSessionInactiveError):
        build_service().execute(
            db_session,
            ResolveAuthenticatedPrincipalCommand(access_token=issued_token.token),
        )


def test_expired_persisted_session_cannot_resolve_principal(
    db_session: Session,
) -> None:
    user = create_user(db_session)
    auth_session = create_authentication_session(
        db_session,
        user=user,
        expires_at=FIXED_NOW,
    )
    issued_token = issue_access_token(
        user_id=user.id,
        session_id=auth_session.id,
    )

    with pytest.raises(AuthenticationSessionExpiredError):
        build_service().execute(
            db_session,
            ResolveAuthenticatedPrincipalCommand(access_token=issued_token.token),
        )


def test_disabled_user_cannot_resolve_principal(
    db_session: Session,
) -> None:
    user = create_user(
        db_session,
        status=UserStatus.DISABLED,
    )
    auth_session = create_authentication_session(
        db_session,
        user=user,
    )
    issued_token = issue_access_token(
        user_id=user.id,
        session_id=auth_session.id,
    )

    with pytest.raises(UserDisabledError):
        build_service().execute(
            db_session,
            ResolveAuthenticatedPrincipalCommand(access_token=issued_token.token),
        )


def test_expired_access_token_fails_before_principal_resolution(
    db_session: Session,
) -> None:
    user = create_user(db_session)
    auth_session = create_authentication_session(
        db_session,
        user=user,
    )
    issued_token = issue_access_token(
        user_id=user.id,
        session_id=auth_session.id,
        issued_at=FIXED_NOW - timedelta(minutes=16),
    )

    with pytest.raises(AccessTokenExpiredError):
        build_service().execute(
            db_session,
            ResolveAuthenticatedPrincipalCommand(access_token=issued_token.token),
        )
