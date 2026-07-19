from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from psycopg.errors import CheckViolation, UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.authentication.models import (
    AuthSession,
    AuthSessionStatus,
    RefreshToken,
    RefreshTokenStatus,
)
from clinicops.db.session import get_engine
from clinicops.identity.models import User

FIXED_NOW = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)
SESSION_EXPIRATION = FIXED_NOW + timedelta(days=30)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def create_user() -> User:
    """Build a unique global user for authentication tests."""

    return User(email=f"user-{uuid4()}@example.com")


def build_auth_session(
    user: User,
    *,
    status: AuthSessionStatus = AuthSessionStatus.ACTIVE,
    expires_at: datetime = SESSION_EXPIRATION,
    revoked_at: datetime | None = None,
    compromised_at: datetime | None = None,
) -> AuthSession:
    """Build an authentication session with deterministic timestamps."""

    return AuthSession(
        user=user,
        status=status,
        expires_at=expires_at,
        last_rotated_at=FIXED_NOW,
        revoked_at=revoked_at,
        compromised_at=compromised_at,
        created_at=FIXED_NOW,
    )


def build_refresh_token(
    auth_session: AuthSession,
    *,
    token_seed: str | None = None,
    status: RefreshTokenStatus = RefreshTokenStatus.ACTIVE,
    expires_at: datetime = SESSION_EXPIRATION,
    consumed_at: datetime | None = None,
    revoked_at: datetime | None = None,
    replaced_by_token_id: UUID | None = None,
) -> RefreshToken:
    """Build a refresh token with deterministic lifecycle state."""

    return RefreshToken(
        auth_session=auth_session,
        token_digest=(token_seed if token_seed is not None else uuid4().hex + uuid4().hex),
        status=status,
        expires_at=expires_at,
        consumed_at=consumed_at,
        revoked_at=revoked_at,
        replaced_by_token_id=replaced_by_token_id,
        created_at=FIXED_NOW,
    )


def assert_unique_violation(
    exception_info: pytest.ExceptionInfo[IntegrityError],
    constraint_name: str,
) -> None:
    """Assert that PostgreSQL rejected a named unique invariant."""

    original_exception = exception_info.value.orig

    assert isinstance(original_exception, UniqueViolation)
    assert original_exception.diag.constraint_name == constraint_name


def assert_check_violation(
    exception_info: pytest.ExceptionInfo[IntegrityError],
    constraint_name: str,
) -> None:
    """Assert that PostgreSQL rejected a named check invariant."""

    original_exception = exception_info.value.orig

    assert isinstance(original_exception, CheckViolation)
    assert original_exception.diag.constraint_name == constraint_name


def test_active_authentication_session_persists(
    db_session: Session,
) -> None:
    user = create_user()
    auth_session = build_auth_session(user)

    db_session.add_all([user, auth_session])
    db_session.flush()

    session_id = auth_session.id
    db_session.expire_all()

    stored_session = db_session.scalar(select(AuthSession).where(AuthSession.id == session_id))

    assert stored_session is not None
    assert stored_session.user_id == user.id
    assert stored_session.status is AuthSessionStatus.ACTIVE
    assert stored_session.expires_at == SESSION_EXPIRATION
    assert stored_session.last_rotated_at == FIXED_NOW
    assert stored_session.revoked_at is None
    assert stored_session.compromised_at is None
    assert stored_session.created_at.tzinfo is not None
    assert stored_session.updated_at.tzinfo is not None


def test_user_may_have_multiple_active_sessions(
    db_session: Session,
) -> None:
    user = create_user()
    first_session = build_auth_session(user)
    second_session = build_auth_session(user)

    db_session.add_all([user, first_session, second_session])
    db_session.flush()

    assert first_session.id != second_session.id
    assert first_session.status is AuthSessionStatus.ACTIVE
    assert second_session.status is AuthSessionStatus.ACTIVE


def test_active_refresh_token_persists(
    db_session: Session,
) -> None:
    user = create_user()
    auth_session = build_auth_session(user)
    refresh_token = build_refresh_token(
        auth_session,
        token_seed="a" * 64,
    )

    db_session.add_all([user, auth_session, refresh_token])
    db_session.flush()

    token_id = refresh_token.id
    db_session.expire_all()

    stored_token = db_session.scalar(select(RefreshToken).where(RefreshToken.id == token_id))

    assert stored_token is not None
    assert stored_token.session_id == auth_session.id
    assert stored_token.token_digest == "a" * 64
    assert stored_token.status is RefreshTokenStatus.ACTIVE
    assert stored_token.expires_at == SESSION_EXPIRATION
    assert stored_token.consumed_at is None
    assert stored_token.revoked_at is None
    assert stored_token.replaced_by_token_id is None
    assert stored_token.created_at.tzinfo is not None


def test_authentication_session_allows_one_active_refresh_token(
    db_session: Session,
) -> None:
    user = create_user()
    auth_session = build_auth_session(user)
    first_token = build_refresh_token(auth_session)

    db_session.add_all([user, auth_session, first_token])
    db_session.flush()

    second_token = build_refresh_token(auth_session)
    db_session.add(second_token)

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_unique_violation(
        exception_info,
        "uq_refresh_tokens_one_active_per_session",
    )


def test_consumed_token_may_reference_active_replacement(
    db_session: Session,
) -> None:
    user = create_user()
    auth_session = build_auth_session(user)
    replacement = build_refresh_token(
        auth_session,
        token_seed="b" * 64,
    )

    db_session.add_all([user, auth_session, replacement])
    db_session.flush()

    consumed_token = build_refresh_token(
        auth_session,
        token_seed="c" * 64,
        status=RefreshTokenStatus.CONSUMED,
        consumed_at=FIXED_NOW + timedelta(minutes=1),
        replaced_by_token_id=replacement.id,
    )
    db_session.add(consumed_token)
    db_session.flush()

    assert consumed_token.status is RefreshTokenStatus.CONSUMED
    assert consumed_token.replaced_by_token_id == replacement.id
    assert consumed_token.replacement is replacement
    assert replacement.status is RefreshTokenStatus.ACTIVE


def test_refresh_token_digest_is_globally_unique(
    db_session: Session,
) -> None:
    user = create_user()
    first_session = build_auth_session(user)
    second_session = build_auth_session(user)
    shared_digest = "d" * 64
    first_token = build_refresh_token(
        first_session,
        token_seed=shared_digest,
    )
    second_token = build_refresh_token(
        second_session,
        token_seed=shared_digest,
    )

    db_session.add_all(
        [
            user,
            first_session,
            second_session,
            first_token,
            second_token,
        ]
    )

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_unique_violation(
        exception_info,
        "uq_refresh_tokens_token_digest",
    )


@pytest.mark.parametrize(
    (
        "status",
        "revoked_at",
        "compromised_at",
    ),
    [
        (
            AuthSessionStatus.ACTIVE,
            FIXED_NOW,
            None,
        ),
        (
            AuthSessionStatus.ACTIVE,
            None,
            FIXED_NOW,
        ),
        (
            AuthSessionStatus.REVOKED,
            None,
            None,
        ),
        (
            AuthSessionStatus.COMPROMISED,
            None,
            None,
        ),
    ],
)
def test_authentication_session_rejects_inconsistent_state(
    db_session: Session,
    status: AuthSessionStatus,
    revoked_at: datetime | None,
    compromised_at: datetime | None,
) -> None:
    user = create_user()
    auth_session = build_auth_session(
        user,
        status=status,
        revoked_at=revoked_at,
        compromised_at=compromised_at,
    )

    db_session.add_all([user, auth_session])

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_check_violation(
        exception_info,
        "ck_auth_sessions_status_consistency",
    )


@pytest.mark.parametrize(
    (
        "status",
        "consumed_at",
        "revoked_at",
    ),
    [
        (
            RefreshTokenStatus.ACTIVE,
            FIXED_NOW,
            None,
        ),
        (
            RefreshTokenStatus.ACTIVE,
            None,
            FIXED_NOW,
        ),
        (
            RefreshTokenStatus.CONSUMED,
            FIXED_NOW,
            None,
        ),
        (
            RefreshTokenStatus.REVOKED,
            None,
            None,
        ),
    ],
)
def test_refresh_token_rejects_inconsistent_state(
    db_session: Session,
    status: RefreshTokenStatus,
    consumed_at: datetime | None,
    revoked_at: datetime | None,
) -> None:
    user = create_user()
    auth_session = build_auth_session(user)
    refresh_token = build_refresh_token(
        auth_session,
        status=status,
        consumed_at=consumed_at,
        revoked_at=revoked_at,
    )

    db_session.add_all([user, auth_session, refresh_token])

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_check_violation(
        exception_info,
        "ck_refresh_tokens_status_consistency",
    )


def test_authentication_session_expiration_must_follow_creation(
    db_session: Session,
) -> None:
    user = create_user()
    auth_session = build_auth_session(
        user,
        expires_at=FIXED_NOW,
    )

    db_session.add_all([user, auth_session])

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_check_violation(
        exception_info,
        "ck_auth_sessions_expires_after_created_at",
    )


def test_refresh_token_expiration_must_follow_creation(
    db_session: Session,
) -> None:
    user = create_user()
    auth_session = build_auth_session(user)
    refresh_token = build_refresh_token(
        auth_session,
        expires_at=FIXED_NOW,
    )

    db_session.add_all([user, auth_session, refresh_token])

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_check_violation(
        exception_info,
        "ck_refresh_tokens_expires_after_created_at",
    )


def test_refresh_token_cannot_replace_itself(
    db_session: Session,
) -> None:
    user = create_user()
    auth_session = build_auth_session(user)
    token_id = uuid4()
    refresh_token = RefreshToken(
        id=token_id,
        auth_session=auth_session,
        token_digest="e" * 64,
        status=RefreshTokenStatus.CONSUMED,
        expires_at=SESSION_EXPIRATION,
        consumed_at=FIXED_NOW + timedelta(minutes=1),
        replaced_by_token_id=token_id,
        created_at=FIXED_NOW,
    )

    db_session.add_all([user, auth_session, refresh_token])

    with pytest.raises(IntegrityError) as exception_info:
        db_session.flush()

    assert_check_violation(
        exception_info,
        "ck_refresh_tokens_not_self_replaced",
    )
