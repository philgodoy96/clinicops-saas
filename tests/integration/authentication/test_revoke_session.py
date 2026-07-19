from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.authentication.exceptions import (
    AuthenticationSessionInactiveError,
    AuthenticationSessionNotFoundError,
)
from clinicops.authentication.models import (
    AuthSession,
    AuthSessionStatus,
    RefreshToken,
    RefreshTokenStatus,
)
from clinicops.authentication.services.revoke_session import (
    RevokeAuthenticationSessionCommand,
    RevokeAuthenticationSessionService,
)
from clinicops.db.session import get_engine
from clinicops.identity.models import User

FIXED_NOW = datetime(2026, 7, 26, 15, 0, tzinfo=UTC)
SESSION_CREATED_AT = FIXED_NOW - timedelta(days=1)
SESSION_EXPIRES_AT = FIXED_NOW + timedelta(days=29)


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


@dataclass(frozen=True, slots=True)
class RevocationFixture:
    """Persisted user session and optional refresh-token history."""

    user: User
    auth_session: AuthSession
    active_token: RefreshToken | None
    consumed_token: RefreshToken | None


@dataclass(frozen=True, slots=True)
class CommittedRevocationFixture:
    """Identifiers for transaction ownership coverage."""

    user_id: UUID
    session_id: UUID
    active_token_id: UUID


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def build_service() -> RevokeAuthenticationSessionService:
    """Build a revocation service with deterministic time."""

    return RevokeAuthenticationSessionService(clock=FixedClock())


def create_revocation_fixture(
    session: Session,
    *,
    session_status: AuthSessionStatus = AuthSessionStatus.ACTIVE,
    include_active_token: bool = True,
    include_consumed_history: bool = False,
) -> RevocationFixture:
    """Persist one authentication session and optional token history."""

    user = User(email=f"user-{uuid4()}@example.com")
    auth_session = AuthSession(
        user=user,
        status=session_status,
        expires_at=SESSION_EXPIRES_AT,
        last_rotated_at=SESSION_CREATED_AT,
        revoked_at=(
            FIXED_NOW - timedelta(hours=1) if session_status is AuthSessionStatus.REVOKED else None
        ),
        compromised_at=(
            FIXED_NOW - timedelta(hours=1)
            if session_status is AuthSessionStatus.COMPROMISED
            else None
        ),
        created_at=SESSION_CREATED_AT,
    )

    active_token: RefreshToken | None = None
    consumed_token: RefreshToken | None = None

    if include_active_token:
        active_token = RefreshToken(
            id=uuid4(),
            auth_session=auth_session,
            token_digest=uuid4().hex + uuid4().hex,
            expires_at=SESSION_EXPIRES_AT,
            created_at=SESSION_CREATED_AT,
        )

    if include_consumed_history:
        assert active_token is not None

        consumed_token = RefreshToken(
            auth_session=auth_session,
            token_digest=uuid4().hex + uuid4().hex,
            status=RefreshTokenStatus.CONSUMED,
            expires_at=SESSION_EXPIRES_AT,
            consumed_at=FIXED_NOW - timedelta(hours=1),
            replaced_by_token_id=active_token.id,
            created_at=SESSION_CREATED_AT,
        )

    entities: list[object] = [user, auth_session]

    if active_token is not None:
        entities.append(active_token)

    if consumed_token is not None:
        entities.append(consumed_token)

    session.add_all(entities)
    session.flush()

    return RevocationFixture(
        user=user,
        auth_session=auth_session,
        active_token=active_token,
        consumed_token=consumed_token,
    )


def test_active_session_revocation_revokes_current_token(
    db_session: Session,
) -> None:
    fixture = create_revocation_fixture(
        db_session,
        include_consumed_history=True,
    )

    result = build_service().execute(
        db_session,
        RevokeAuthenticationSessionCommand(
            user_id=fixture.user.id,
            session_id=fixture.auth_session.id,
        ),
    )

    assert result.session_id == fixture.auth_session.id
    assert result.revoked_at == FIXED_NOW

    assert fixture.auth_session.status is AuthSessionStatus.REVOKED
    assert fixture.auth_session.revoked_at == FIXED_NOW
    assert fixture.auth_session.compromised_at is None

    assert fixture.active_token is not None
    assert fixture.active_token.status is RefreshTokenStatus.REVOKED
    assert fixture.active_token.revoked_at == FIXED_NOW
    assert fixture.active_token.consumed_at is None
    assert fixture.active_token.replaced_by_token_id is None

    assert fixture.consumed_token is not None
    assert fixture.consumed_token.status is RefreshTokenStatus.CONSUMED
    assert fixture.consumed_token.revoked_at is None
    assert fixture.consumed_token.consumed_at is not None
    assert fixture.consumed_token.replaced_by_token_id == fixture.active_token.id


def test_active_session_without_active_token_can_be_revoked(
    db_session: Session,
) -> None:
    fixture = create_revocation_fixture(
        db_session,
        include_active_token=False,
    )

    result = build_service().execute(
        db_session,
        RevokeAuthenticationSessionCommand(
            user_id=fixture.user.id,
            session_id=fixture.auth_session.id,
        ),
    )

    assert result.session_id == fixture.auth_session.id
    assert fixture.auth_session.status is AuthSessionStatus.REVOKED
    assert fixture.auth_session.revoked_at == FIXED_NOW


def test_user_cannot_revoke_another_users_session(
    db_session: Session,
) -> None:
    target_fixture = create_revocation_fixture(db_session)
    other_user = User(email=f"other-{uuid4()}@example.com")
    db_session.add(other_user)
    db_session.flush()

    with pytest.raises(AuthenticationSessionNotFoundError):
        build_service().execute(
            db_session,
            RevokeAuthenticationSessionCommand(
                user_id=other_user.id,
                session_id=target_fixture.auth_session.id,
            ),
        )

    assert target_fixture.auth_session.status is AuthSessionStatus.ACTIVE
    assert target_fixture.auth_session.revoked_at is None

    assert target_fixture.active_token is not None
    assert target_fixture.active_token.status is RefreshTokenStatus.ACTIVE


def test_missing_user_rejects_session_revocation(
    db_session: Session,
) -> None:
    with pytest.raises(AuthenticationSessionNotFoundError):
        build_service().execute(
            db_session,
            RevokeAuthenticationSessionCommand(
                user_id=uuid4(),
                session_id=uuid4(),
            ),
        )


def test_missing_session_rejects_session_revocation(
    db_session: Session,
) -> None:
    user = User(email=f"user-{uuid4()}@example.com")
    db_session.add(user)
    db_session.flush()

    with pytest.raises(AuthenticationSessionNotFoundError):
        build_service().execute(
            db_session,
            RevokeAuthenticationSessionCommand(
                user_id=user.id,
                session_id=uuid4(),
            ),
        )


@pytest.mark.parametrize(
    "session_status",
    [
        AuthSessionStatus.REVOKED,
        AuthSessionStatus.COMPROMISED,
    ],
)
def test_inactive_session_cannot_be_revoked_again(
    db_session: Session,
    session_status: AuthSessionStatus,
) -> None:
    fixture = create_revocation_fixture(
        db_session,
        session_status=session_status,
        include_active_token=False,
    )

    with pytest.raises(AuthenticationSessionInactiveError):
        build_service().execute(
            db_session,
            RevokeAuthenticationSessionCommand(
                user_id=fixture.user.id,
                session_id=fixture.auth_session.id,
            ),
        )


def persist_committed_revocation_fixture() -> CommittedRevocationFixture:
    """Persist one active session for rollback coverage."""

    with Session(get_engine()) as session:
        fixture = create_revocation_fixture(session)
        assert fixture.active_token is not None

        committed_fixture = CommittedRevocationFixture(
            user_id=fixture.user.id,
            session_id=fixture.auth_session.id,
            active_token_id=fixture.active_token.id,
        )
        session.commit()

    return committed_fixture


def delete_committed_revocation_fixture(
    fixture: CommittedRevocationFixture,
) -> None:
    """Delete a committed authentication fixture."""

    with Session(get_engine()) as session:
        session.execute(delete(AuthSession).where(AuthSession.id == fixture.session_id))
        session.execute(delete(User).where(User.id == fixture.user_id))
        session.commit()


def test_session_revocation_does_not_commit_transaction() -> None:
    fixture = persist_committed_revocation_fixture()

    try:
        with Session(get_engine()) as session:
            build_service().execute(
                session,
                RevokeAuthenticationSessionCommand(
                    user_id=fixture.user_id,
                    session_id=fixture.session_id,
                ),
            )
            session.rollback()

        with Session(get_engine()) as verification_session:
            auth_session = verification_session.get(
                AuthSession,
                fixture.session_id,
            )
            active_token = verification_session.get(
                RefreshToken,
                fixture.active_token_id,
            )

        assert auth_session is not None
        assert auth_session.status is AuthSessionStatus.ACTIVE
        assert auth_session.revoked_at is None

        assert active_token is not None
        assert active_token.status is RefreshTokenStatus.ACTIVE
        assert active_token.revoked_at is None
    finally:
        delete_committed_revocation_fixture(fixture)
