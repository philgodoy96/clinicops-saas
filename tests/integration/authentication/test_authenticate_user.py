from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.authentication.access_tokens import AccessTokenCodec
from clinicops.authentication.config import (
    ACCESS_TOKEN_LIFETIME,
    AUTHENTICATION_SESSION_LIFETIME,
    AuthenticationTokenConfig,
)
from clinicops.authentication.exceptions import InvalidCredentialsError
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
from clinicops.authentication.services.authenticate_user import (
    DUMMY_PASSWORD_HASH,
    AuthenticateUserCommand,
    AuthenticateUserService,
)
from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import (
    Argon2PasswordHasher,
    PasswordHasher,
)

FIXED_NOW = datetime(2026, 7, 24, 15, 0, tzinfo=UTC)
SIGNING_KEY = "development-signing-key-with-32-bytes-minimum"
PASSWORD = "Correct horse battery staple"
FIXED_REFRESH_TOKEN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
FIXED_REFRESH_TOKEN_PLAINTEXT = f"{FIXED_REFRESH_TOKEN_ID}.{'a' * 43}"
FIXED_REFRESH_TOKEN = GeneratedRefreshToken(
    token_id=FIXED_REFRESH_TOKEN_ID,
    plaintext=FIXED_REFRESH_TOKEN_PLAINTEXT,
    digest=digest_refresh_token(FIXED_REFRESH_TOKEN_PLAINTEXT),
)


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


class TrackingPasswordHasher:
    """Record password verification without performing real hashing."""

    def __init__(self, verify_result: bool) -> None:
        self.verify_result = verify_result
        self.verify_calls: list[tuple[str, str]] = []

    def hash(self, password: str) -> str:
        raise AssertionError("Hashing is not expected during login.")

    def verify(self, password: str, encoded_hash: str) -> bool:
        self.verify_calls.append((password, encoded_hash))
        return self.verify_result

    def needs_rehash(self, encoded_hash: str) -> bool:
        return False


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
    """Build deterministic access token configuration."""

    return AccessTokenCodec(
        AuthenticationTokenConfig(
            issuer="clinicops",
            audience="clinicops-api",
            signing_key=SIGNING_KEY,
        )
    )


def fixed_refresh_token_factory() -> GeneratedRefreshToken:
    """Return a deterministic refresh token."""

    return FIXED_REFRESH_TOKEN


def build_service(
    *,
    password_hasher: PasswordHasher | None = None,
) -> AuthenticateUserService:
    """Build the authentication service with deterministic dependencies."""

    return AuthenticateUserService(
        access_token_codec=build_access_token_codec(),
        password_hasher=password_hasher,
        clock=FixedClock(),
        refresh_token_factory=fixed_refresh_token_factory,
    )


def create_password_user(
    session: Session,
    *,
    email: str,
    password: str = PASSWORD,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    """Persist one global user with an Argon2id credential."""

    user = User(
        email=email,
        status=status,
        disabled_at=(FIXED_NOW if status is UserStatus.DISABLED else None),
        password_credential=PasswordCredential(
            password_hash=Argon2PasswordHasher().hash(password),
        ),
    )
    session.add(user)
    session.flush()

    return user


def count_authentication_sessions(
    session: Session,
    *,
    user_id: UUID | None = None,
) -> int:
    """Return the number of authentication sessions."""

    statement = select(func.count()).select_from(AuthSession)
    if user_id is not None:
        statement = statement.where(AuthSession.user_id == user_id)

    count = session.scalar(statement)
    assert count is not None
    return count


def test_valid_credentials_create_global_authentication_session(
    db_session: Session,
) -> None:
    user = create_password_user(
        db_session,
        email="user@example.com",
    )
    command = AuthenticateUserCommand(
        email="  USER@EXAMPLE.COM  ",
        password=PASSWORD,
    )

    result = build_service().execute(db_session, command)

    stored_session = db_session.get(AuthSession, result.session_id)
    stored_refresh_token = db_session.get(
        RefreshToken,
        FIXED_REFRESH_TOKEN_ID,
    )
    access_claims = build_access_token_codec().decode(
        result.access_token,
        now=FIXED_NOW,
    )

    assert result.user_id == user.id
    assert result.access_token_expires_at == (FIXED_NOW + ACCESS_TOKEN_LIFETIME)
    assert result.refresh_token == FIXED_REFRESH_TOKEN_PLAINTEXT
    assert result.session_expires_at == (FIXED_NOW + AUTHENTICATION_SESSION_LIFETIME)
    assert PASSWORD not in repr(command)
    assert result.access_token not in repr(result)
    assert result.refresh_token not in repr(result)

    assert access_claims.user_id == user.id
    assert access_claims.session_id == result.session_id

    assert stored_session is not None
    assert stored_session.user_id == user.id
    assert stored_session.status is AuthSessionStatus.ACTIVE
    assert stored_session.created_at == FIXED_NOW
    assert stored_session.last_rotated_at == FIXED_NOW
    assert stored_session.expires_at == result.session_expires_at
    assert stored_session.revoked_at is None
    assert stored_session.compromised_at is None

    assert stored_refresh_token is not None
    assert stored_refresh_token.session_id == result.session_id
    assert stored_refresh_token.status is RefreshTokenStatus.ACTIVE
    assert stored_refresh_token.token_digest == (FIXED_REFRESH_TOKEN.digest)
    assert stored_refresh_token.expires_at == (result.session_expires_at)
    assert FIXED_REFRESH_TOKEN_PLAINTEXT not in stored_refresh_token.token_digest


def test_unknown_email_performs_dummy_password_verification(
    db_session: Session,
) -> None:
    password_hasher = TrackingPasswordHasher(verify_result=False)
    service = build_service(password_hasher=password_hasher)
    session_count_before = count_authentication_sessions(db_session)

    with pytest.raises(InvalidCredentialsError):
        service.execute(
            db_session,
            AuthenticateUserCommand(
                email="unknown@example.com",
                password=PASSWORD,
            ),
        )

    assert password_hasher.verify_calls == [(PASSWORD, DUMMY_PASSWORD_HASH)]
    assert count_authentication_sessions(db_session) == session_count_before


def test_incorrect_password_uses_generic_authentication_failure(
    db_session: Session,
) -> None:
    user = create_password_user(
        db_session,
        email="wrong-password@example.com",
    )

    with pytest.raises(InvalidCredentialsError) as exception_info:
        build_service().execute(
            db_session,
            AuthenticateUserCommand(
                email="wrong-password@example.com",
                password="This password is definitely incorrect",
            ),
        )

    assert exception_info.value.code == "invalid_credentials"
    assert exception_info.value.public_message == ("The email or password is invalid.")
    assert count_authentication_sessions(db_session, user_id=user.id) == 0


def test_user_without_password_credential_cannot_authenticate(
    db_session: Session,
) -> None:
    user = User(email="credentialless@example.com")
    db_session.add(user)
    db_session.flush()

    with pytest.raises(InvalidCredentialsError):
        build_service().execute(
            db_session,
            AuthenticateUserCommand(
                email=user.email,
                password=PASSWORD,
            ),
        )

    assert count_authentication_sessions(db_session, user_id=user.id) == 0


def test_disabled_user_cannot_authenticate(
    db_session: Session,
) -> None:
    user = create_password_user(
        db_session,
        email="disabled@example.com",
        status=UserStatus.DISABLED,
    )

    with pytest.raises(InvalidCredentialsError):
        build_service().execute(
            db_session,
            AuthenticateUserCommand(
                email=user.email,
                password=PASSWORD,
            ),
        )

    assert count_authentication_sessions(db_session, user_id=user.id) == 0


def persist_committed_user() -> UUID:
    """Persist a user for transaction ownership coverage."""

    with Session(get_engine()) as session:
        user = create_password_user(
            session,
            email=f"rollback-{uuid4()}@example.com",
        )
        user_id = user.id
        session.commit()

    return user_id


def delete_committed_user(user_id: UUID) -> None:
    """Delete a committed user and any unexpected sessions."""

    with Session(get_engine()) as session:
        session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


def test_authentication_service_does_not_commit_transaction() -> None:
    user_id = persist_committed_user()

    try:
        with Session(get_engine()) as session:
            user = session.get(User, user_id)
            assert user is not None

            result = build_service().execute(
                session,
                AuthenticateUserCommand(
                    email=user.email,
                    password=PASSWORD,
                ),
            )
            session_id = result.session_id
            refresh_token_id = FIXED_REFRESH_TOKEN_ID
            session.rollback()

        with Session(get_engine()) as verification_session:
            stored_session = verification_session.get(
                AuthSession,
                session_id,
            )
            stored_refresh_token = verification_session.get(
                RefreshToken,
                refresh_token_id,
            )

        assert stored_session is None
        assert stored_refresh_token is None
    finally:
        delete_committed_user(user_id)
