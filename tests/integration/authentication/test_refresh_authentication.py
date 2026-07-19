from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from clinicops.authentication.access_tokens import AccessTokenCodec
from clinicops.authentication.config import AuthenticationTokenConfig
from clinicops.authentication.exceptions import (
    AuthenticationSessionInactiveError,
    RefreshTokenExpiredError,
    RefreshTokenInvalidError,
)
from clinicops.authentication.models import (
    AuthSession,
    AuthSessionStatus,
    RefreshToken,
    RefreshTokenStatus,
)
from clinicops.authentication.refresh_tokens import (
    GeneratedRefreshToken,
    digest_refresh_token,
)
from clinicops.authentication.services.refresh_authentication import (
    AuthenticationCompromiseReason,
    CompromisedAuthenticationSession,
    RefreshAuthenticationCommand,
    RefreshAuthenticationService,
    RefreshedAuthentication,
)
from clinicops.db.session import get_engine
from clinicops.identity.exceptions import UserDisabledError
from clinicops.identity.models import User, UserStatus

FIXED_NOW = datetime(2026, 7, 25, 15, 0, tzinfo=UTC)
SESSION_CREATED_AT = FIXED_NOW - timedelta(days=1)
SESSION_EXPIRES_AT = FIXED_NOW + timedelta(days=29)
SIGNING_KEY = "development-signing-key-with-32-bytes-minimum"


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


@dataclass(frozen=True, slots=True)
class AuthenticationFixture:
    """Persisted authentication state and plaintext test tokens."""

    user: User
    auth_session: AuthSession
    current_token: RefreshToken
    current_plaintext: str
    active_replacement: RefreshToken | None = None
    active_replacement_plaintext: str | None = None


@dataclass(frozen=True, slots=True)
class CommittedAuthenticationFixture:
    """Identifiers for committed transaction-boundary coverage."""

    user_id: UUID
    session_id: UUID
    current_token_id: UUID
    current_plaintext: str
    active_replacement_id: UUID | None = None


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def build_plaintext_token(
    token_id: UUID,
    secret_character: str,
) -> str:
    """Build a structurally valid deterministic refresh token."""

    return f"{token_id}.{secret_character * 43}"


def build_generated_token(
    secret_character: str = "b",
) -> GeneratedRefreshToken:
    """Build a deterministic replacement token."""

    token_id = uuid4()
    plaintext = build_plaintext_token(
        token_id,
        secret_character,
    )

    return GeneratedRefreshToken(
        token_id=token_id,
        plaintext=plaintext,
        digest=digest_refresh_token(plaintext),
    )


def build_access_token_codec() -> AccessTokenCodec:
    """Build deterministic access token configuration."""

    return AccessTokenCodec(
        AuthenticationTokenConfig(
            issuer="clinicops",
            audience="clinicops-api",
            signing_key=SIGNING_KEY,
        )
    )


def build_service(
    replacement: GeneratedRefreshToken,
) -> RefreshAuthenticationService:
    """Build a refresh service with deterministic dependencies."""

    return RefreshAuthenticationService(
        access_token_codec=build_access_token_codec(),
        clock=FixedClock(),
        refresh_token_factory=lambda: replacement,
    )


def create_active_authentication_fixture(
    session: Session,
    *,
    user_status: UserStatus = UserStatus.ACTIVE,
    session_status: AuthSessionStatus = AuthSessionStatus.ACTIVE,
    session_expires_at: datetime = SESSION_EXPIRES_AT,
    token_expires_at: datetime = SESSION_EXPIRES_AT,
) -> AuthenticationFixture:
    """Persist one session with one active refresh token."""

    user = User(
        email=f"user-{uuid4()}@example.com",
        status=user_status,
        disabled_at=(FIXED_NOW if user_status is UserStatus.DISABLED else None),
    )
    auth_session = AuthSession(
        user=user,
        status=session_status,
        expires_at=session_expires_at,
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
    token_id = uuid4()
    plaintext = build_plaintext_token(token_id, "a")
    refresh_token = RefreshToken(
        id=token_id,
        auth_session=auth_session,
        token_digest=digest_refresh_token(plaintext),
        expires_at=token_expires_at,
        created_at=SESSION_CREATED_AT,
    )

    session.add_all([user, auth_session, refresh_token])
    session.flush()

    return AuthenticationFixture(
        user=user,
        auth_session=auth_session,
        current_token=refresh_token,
        current_plaintext=plaintext,
    )


def create_consumed_authentication_fixture(
    session: Session,
) -> AuthenticationFixture:
    """Persist a consumed token and its current active replacement."""

    user = User(email=f"user-{uuid4()}@example.com")
    auth_session = AuthSession(
        user=user,
        expires_at=SESSION_EXPIRES_AT,
        last_rotated_at=FIXED_NOW - timedelta(minutes=5),
        created_at=SESSION_CREATED_AT,
    )

    active_token_id = uuid4()
    active_plaintext = build_plaintext_token(
        active_token_id,
        "c",
    )
    active_replacement = RefreshToken(
        id=active_token_id,
        auth_session=auth_session,
        token_digest=digest_refresh_token(active_plaintext),
        expires_at=SESSION_EXPIRES_AT,
        created_at=FIXED_NOW - timedelta(minutes=5),
    )
    consumed_token_id = uuid4()
    consumed_plaintext = build_plaintext_token(
        consumed_token_id,
        "a",
    )
    consumed_token = RefreshToken(
        id=consumed_token_id,
        auth_session=auth_session,
        token_digest=digest_refresh_token(consumed_plaintext),
        status=RefreshTokenStatus.CONSUMED,
        expires_at=SESSION_EXPIRES_AT,
        consumed_at=FIXED_NOW - timedelta(minutes=5),
        replaced_by_token_id=active_token_id,
        created_at=SESSION_CREATED_AT,
    )

    session.add_all(
        [
            user,
            auth_session,
            active_replacement,
            consumed_token,
        ]
    )
    session.flush()

    return AuthenticationFixture(
        user=user,
        auth_session=auth_session,
        current_token=consumed_token,
        current_plaintext=consumed_plaintext,
        active_replacement=active_replacement,
        active_replacement_plaintext=active_plaintext,
    )


def test_active_refresh_token_rotates_without_extending_session(
    db_session: Session,
) -> None:
    fixture = create_active_authentication_fixture(db_session)
    replacement = build_generated_token()
    command = RefreshAuthenticationCommand(refresh_token=fixture.current_plaintext)

    result = build_service(replacement).execute(
        db_session,
        command,
    )

    assert isinstance(result, RefreshedAuthentication)
    assert result.user_id == fixture.user.id
    assert result.session_id == fixture.auth_session.id
    assert result.refresh_token == replacement.plaintext
    assert result.session_expires_at == SESSION_EXPIRES_AT
    assert result.access_token not in repr(result)
    assert result.refresh_token not in repr(result)
    assert fixture.current_plaintext not in repr(command)

    access_claims = build_access_token_codec().decode(
        result.access_token,
        now=FIXED_NOW,
    )

    assert access_claims.user_id == fixture.user.id
    assert access_claims.session_id == fixture.auth_session.id

    stored_replacement = db_session.get(
        RefreshToken,
        replacement.token_id,
    )

    assert fixture.current_token.status is RefreshTokenStatus.CONSUMED
    assert fixture.current_token.consumed_at == FIXED_NOW
    assert fixture.current_token.replaced_by_token_id == replacement.token_id
    assert fixture.auth_session.status is AuthSessionStatus.ACTIVE
    assert fixture.auth_session.last_rotated_at == FIXED_NOW
    assert fixture.auth_session.expires_at == SESSION_EXPIRES_AT

    assert stored_replacement is not None
    assert stored_replacement.status is RefreshTokenStatus.ACTIVE
    assert stored_replacement.session_id == fixture.auth_session.id
    assert stored_replacement.expires_at == SESSION_EXPIRES_AT
    assert stored_replacement.token_digest == replacement.digest
    assert replacement.plaintext not in stored_replacement.token_digest


def test_wrong_secret_does_not_change_authentication_state(
    db_session: Session,
) -> None:
    fixture = create_active_authentication_fixture(db_session)
    wrong_plaintext = build_plaintext_token(
        fixture.current_token.id,
        "z",
    )

    with pytest.raises(RefreshTokenInvalidError):
        build_service(build_generated_token()).execute(
            db_session,
            RefreshAuthenticationCommand(refresh_token=wrong_plaintext),
        )

    assert fixture.auth_session.status is AuthSessionStatus.ACTIVE
    assert fixture.current_token.status is RefreshTokenStatus.ACTIVE
    assert fixture.current_token.consumed_at is None
    assert fixture.current_token.replaced_by_token_id is None


def test_unknown_refresh_token_is_rejected(
    db_session: Session,
) -> None:
    unknown_plaintext = build_plaintext_token(uuid4(), "a")

    with pytest.raises(RefreshTokenInvalidError):
        build_service(build_generated_token()).execute(
            db_session,
            RefreshAuthenticationCommand(refresh_token=unknown_plaintext),
        )


@pytest.mark.parametrize(
    "session_status",
    [
        AuthSessionStatus.REVOKED,
        AuthSessionStatus.COMPROMISED,
    ],
)
def test_inactive_session_cannot_rotate(
    db_session: Session,
    session_status: AuthSessionStatus,
) -> None:
    fixture = create_active_authentication_fixture(
        db_session,
        session_status=session_status,
    )

    with pytest.raises(AuthenticationSessionInactiveError):
        build_service(build_generated_token()).execute(
            db_session,
            RefreshAuthenticationCommand(refresh_token=fixture.current_plaintext),
        )


def test_disabled_user_cannot_rotate(
    db_session: Session,
) -> None:
    fixture = create_active_authentication_fixture(
        db_session,
        user_status=UserStatus.DISABLED,
    )

    with pytest.raises(UserDisabledError):
        build_service(build_generated_token()).execute(
            db_session,
            RefreshAuthenticationCommand(refresh_token=fixture.current_plaintext),
        )


@pytest.mark.parametrize(
    ("session_expires_at", "token_expires_at"),
    [
        (FIXED_NOW, SESSION_EXPIRES_AT),
        (SESSION_EXPIRES_AT, FIXED_NOW),
    ],
)
def test_expired_session_or_token_cannot_rotate(
    db_session: Session,
    session_expires_at: datetime,
    token_expires_at: datetime,
) -> None:
    fixture = create_active_authentication_fixture(
        db_session,
        session_expires_at=session_expires_at,
        token_expires_at=token_expires_at,
    )

    with pytest.raises(RefreshTokenExpiredError):
        build_service(build_generated_token()).execute(
            db_session,
            RefreshAuthenticationCommand(refresh_token=fixture.current_plaintext),
        )


def test_consumed_token_reuse_compromises_session(
    db_session: Session,
) -> None:
    fixture = create_consumed_authentication_fixture(db_session)

    result = build_service(build_generated_token()).execute(
        db_session,
        RefreshAuthenticationCommand(refresh_token=fixture.current_plaintext),
    )

    assert isinstance(result, CompromisedAuthenticationSession)
    assert result.session_id == fixture.auth_session.id
    assert result.compromised_at == FIXED_NOW
    assert result.reason is (AuthenticationCompromiseReason.REFRESH_TOKEN_REUSE)

    assert fixture.auth_session.status is AuthSessionStatus.COMPROMISED
    assert fixture.auth_session.compromised_at == FIXED_NOW
    assert fixture.auth_session.revoked_at is None
    assert fixture.current_token.status is RefreshTokenStatus.CONSUMED

    assert fixture.active_replacement is not None
    assert fixture.active_replacement.status is RefreshTokenStatus.REVOKED
    assert fixture.active_replacement.revoked_at == FIXED_NOW


def test_wrong_secret_for_consumed_token_does_not_compromise_session(
    db_session: Session,
) -> None:
    fixture = create_consumed_authentication_fixture(db_session)
    wrong_plaintext = build_plaintext_token(
        fixture.current_token.id,
        "z",
    )

    with pytest.raises(RefreshTokenInvalidError):
        build_service(build_generated_token()).execute(
            db_session,
            RefreshAuthenticationCommand(refresh_token=wrong_plaintext),
        )

    assert fixture.auth_session.status is AuthSessionStatus.ACTIVE
    assert fixture.auth_session.compromised_at is None

    assert fixture.active_replacement is not None
    assert fixture.active_replacement.status is RefreshTokenStatus.ACTIVE
    assert fixture.active_replacement.revoked_at is None


def test_replacement_foreign_key_is_initially_deferred(
    db_session: Session,
) -> None:
    row = db_session.execute(
        text(
            """
            SELECT condeferrable, condeferred
            FROM pg_constraint
            WHERE conname =
                'fk_refresh_tokens_replaced_by_token_id_refresh_tokens'
            """
        )
    ).one()

    assert row.condeferrable is True
    assert row.condeferred is True


def persist_active_fixture() -> CommittedAuthenticationFixture:
    """Persist one active session for commit and rollback coverage."""

    with Session(get_engine()) as session:
        fixture = create_active_authentication_fixture(session)
        committed = CommittedAuthenticationFixture(
            user_id=fixture.user.id,
            session_id=fixture.auth_session.id,
            current_token_id=fixture.current_token.id,
            current_plaintext=fixture.current_plaintext,
        )
        session.commit()

    return committed


def persist_consumed_fixture() -> CommittedAuthenticationFixture:
    """Persist one rotated token family for compromise coverage."""

    with Session(get_engine()) as session:
        fixture = create_consumed_authentication_fixture(session)
        assert fixture.active_replacement is not None

        committed = CommittedAuthenticationFixture(
            user_id=fixture.user.id,
            session_id=fixture.auth_session.id,
            current_token_id=fixture.current_token.id,
            current_plaintext=fixture.current_plaintext,
            active_replacement_id=fixture.active_replacement.id,
        )
        session.commit()

    return committed


def delete_committed_fixture(
    fixture: CommittedAuthenticationFixture,
) -> None:
    """Delete a committed authentication fixture."""

    with Session(get_engine()) as session:
        session.execute(delete(AuthSession).where(AuthSession.id == fixture.session_id))
        session.execute(delete(User).where(User.id == fixture.user_id))
        session.commit()


def test_rotation_can_be_committed_with_valid_replacement_link() -> None:
    fixture = persist_active_fixture()
    replacement = build_generated_token()

    try:
        with Session(get_engine()) as session:
            result = build_service(replacement).execute(
                session,
                RefreshAuthenticationCommand(refresh_token=fixture.current_plaintext),
            )
            assert isinstance(result, RefreshedAuthentication)
            session.commit()

        with Session(get_engine()) as verification_session:
            current_token = verification_session.get(
                RefreshToken,
                fixture.current_token_id,
            )
            replacement_token = verification_session.get(
                RefreshToken,
                replacement.token_id,
            )

        assert current_token is not None
        assert current_token.status is RefreshTokenStatus.CONSUMED
        assert current_token.replaced_by_token_id == replacement.token_id

        assert replacement_token is not None
        assert replacement_token.status is RefreshTokenStatus.ACTIVE
    finally:
        delete_committed_fixture(fixture)


def test_successful_rotation_does_not_commit_transaction() -> None:
    fixture = persist_active_fixture()
    replacement = build_generated_token()

    try:
        with Session(get_engine()) as session:
            result = build_service(replacement).execute(
                session,
                RefreshAuthenticationCommand(refresh_token=fixture.current_plaintext),
            )
            assert isinstance(result, RefreshedAuthentication)
            session.rollback()

        with Session(get_engine()) as verification_session:
            current_token = verification_session.get(
                RefreshToken,
                fixture.current_token_id,
            )
            replacement_token = verification_session.get(
                RefreshToken,
                replacement.token_id,
            )

        assert current_token is not None
        assert current_token.status is RefreshTokenStatus.ACTIVE
        assert current_token.consumed_at is None
        assert current_token.replaced_by_token_id is None
        assert replacement_token is None
    finally:
        delete_committed_fixture(fixture)


def test_compromise_transition_does_not_commit_transaction() -> None:
    fixture = persist_consumed_fixture()
    assert fixture.active_replacement_id is not None

    try:
        with Session(get_engine()) as session:
            result = build_service(build_generated_token()).execute(
                session,
                RefreshAuthenticationCommand(refresh_token=fixture.current_plaintext),
            )
            assert isinstance(
                result,
                CompromisedAuthenticationSession,
            )
            session.rollback()

        with Session(get_engine()) as verification_session:
            auth_session = verification_session.get(
                AuthSession,
                fixture.session_id,
            )
            active_replacement = verification_session.get(
                RefreshToken,
                fixture.active_replacement_id,
            )

        assert auth_session is not None
        assert auth_session.status is AuthSessionStatus.ACTIVE
        assert auth_session.compromised_at is None

        assert active_replacement is not None
        assert active_replacement.status is RefreshTokenStatus.ACTIVE
        assert active_replacement.revoked_at is None
    finally:
        delete_committed_fixture(fixture)
