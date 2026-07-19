from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.authentication.access_tokens import AccessTokenCodec
from clinicops.authentication.config import AuthenticationTokenConfig
from clinicops.authentication.exceptions import (
    AuthenticationSessionInactiveError,
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
    CompromisedAuthenticationSession,
    RefreshAuthenticationCommand,
    RefreshAuthenticationService,
    RefreshedAuthentication,
)
from clinicops.authentication.services.revoke_session import (
    RevokeAuthenticationSessionCommand,
    RevokeAuthenticationSessionService,
)
from clinicops.db.session import get_engine
from clinicops.identity.models import User

FIXED_NOW = datetime(2026, 7, 27, 15, 0, tzinfo=UTC)
SESSION_CREATED_AT = FIXED_NOW - timedelta(days=1)
SESSION_EXPIRES_AT = FIXED_NOW + timedelta(days=29)
SIGNING_KEY = "development-signing-key-with-32-bytes-minimum"
ACTIVE_REFRESH_TOKEN_UNIQUE_INDEX = "uq_refresh_tokens_one_active_per_session"


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


@dataclass(frozen=True, slots=True)
class ActiveSessionFixture:
    """Committed session containing one active refresh token."""

    user_id: UUID
    session_id: UUID
    active_token_id: UUID
    active_plaintext: str


@dataclass(frozen=True, slots=True)
class RotatedSessionFixture:
    """Committed session containing consumed and active tokens."""

    user_id: UUID
    session_id: UUID
    consumed_token_id: UUID
    consumed_plaintext: str
    active_token_id: UUID
    active_plaintext: str


@dataclass(frozen=True, slots=True)
class SessionWithoutTokenFixture:
    """Committed session used for direct persistence races."""

    user_id: UUID
    session_id: UUID


def build_plaintext_token(
    token_id: UUID,
    secret_character: str,
) -> str:
    """Build a structurally valid deterministic refresh token."""

    return f"{token_id}.{secret_character * 43}"


def build_generated_token(
    secret_character: str,
) -> GeneratedRefreshToken:
    """Build one unique deterministic-shape replacement token."""

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
    """Build access-token configuration for concurrent services."""

    return AccessTokenCodec(
        AuthenticationTokenConfig(
            issuer="clinicops",
            audience="clinicops-api",
            signing_key=SIGNING_KEY,
        )
    )


def build_refresh_service(
    replacement: GeneratedRefreshToken,
) -> RefreshAuthenticationService:
    """Build a refresh service with deterministic time and token."""

    return RefreshAuthenticationService(
        access_token_codec=build_access_token_codec(),
        clock=FixedClock(),
        refresh_token_factory=lambda: replacement,
    )


def build_revocation_service() -> RevokeAuthenticationSessionService:
    """Build a session revocation service with deterministic time."""

    return RevokeAuthenticationSessionService(clock=FixedClock())


def persist_active_session_fixture() -> ActiveSessionFixture:
    """Persist a session with exactly one active refresh token."""

    with Session(get_engine()) as session:
        user = User(email=f"user-{uuid4()}@example.com")
        auth_session = AuthSession(
            user=user,
            expires_at=SESSION_EXPIRES_AT,
            last_rotated_at=SESSION_CREATED_AT,
            created_at=SESSION_CREATED_AT,
        )
        active_token_id = uuid4()
        active_plaintext = build_plaintext_token(
            active_token_id,
            "a",
        )
        active_token = RefreshToken(
            id=active_token_id,
            auth_session=auth_session,
            token_digest=digest_refresh_token(active_plaintext),
            expires_at=SESSION_EXPIRES_AT,
            created_at=SESSION_CREATED_AT,
        )

        session.add_all([user, auth_session, active_token])
        session.flush()

        fixture = ActiveSessionFixture(
            user_id=user.id,
            session_id=auth_session.id,
            active_token_id=active_token.id,
            active_plaintext=active_plaintext,
        )
        session.commit()

    return fixture


def persist_rotated_session_fixture() -> RotatedSessionFixture:
    """Persist one consumed token and its active replacement."""

    with Session(get_engine()) as session:
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
            "b",
        )
        active_token = RefreshToken(
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
                active_token,
                consumed_token,
            ]
        )
        session.flush()

        fixture = RotatedSessionFixture(
            user_id=user.id,
            session_id=auth_session.id,
            consumed_token_id=consumed_token.id,
            consumed_plaintext=consumed_plaintext,
            active_token_id=active_token.id,
            active_plaintext=active_plaintext,
        )
        session.commit()

    return fixture


def persist_session_without_token_fixture() -> SessionWithoutTokenFixture:
    """Persist an active session without a refresh token."""

    with Session(get_engine()) as session:
        user = User(email=f"user-{uuid4()}@example.com")
        auth_session = AuthSession(
            user=user,
            expires_at=SESSION_EXPIRES_AT,
            last_rotated_at=SESSION_CREATED_AT,
            created_at=SESSION_CREATED_AT,
        )

        session.add_all([user, auth_session])
        session.flush()

        fixture = SessionWithoutTokenFixture(
            user_id=user.id,
            session_id=auth_session.id,
        )
        session.commit()

    return fixture


def delete_committed_fixture(
    user_id: UUID,
    session_id: UUID,
) -> None:
    """Delete one committed authentication concurrency fixture."""

    with Session(get_engine()) as session:
        session.execute(delete(AuthSession).where(AuthSession.id == session_id))
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


def load_session_tokens(
    session_id: UUID,
) -> tuple[AuthSession, list[RefreshToken]]:
    """Load one session and all refresh tokens in creation order."""

    with Session(get_engine()) as session:
        auth_session = session.get(AuthSession, session_id)
        tokens = list(
            session.scalars(
                select(RefreshToken)
                .where(RefreshToken.session_id == session_id)
                .order_by(RefreshToken.created_at, RefreshToken.id)
            )
        )

        assert auth_session is not None

        session.expunge(auth_session)

        for token in tokens:
            session.expunge(token)

    return auth_session, tokens


def test_concurrent_refresh_of_same_token_compromises_family() -> None:
    fixture = persist_active_session_fixture()
    barrier = Barrier(2)

    def refresh_token(worker_id: int) -> str:
        replacement = build_generated_token("b" if worker_id == 0 else "c")
        service = build_refresh_service(replacement)

        with Session(get_engine()) as session:
            barrier.wait(timeout=10)
            result = service.execute(
                session,
                RefreshAuthenticationCommand(refresh_token=fixture.active_plaintext),
            )
            session.commit()

        if isinstance(result, RefreshedAuthentication):
            return "refreshed"

        assert isinstance(
            result,
            CompromisedAuthenticationSession,
        )
        return "compromised"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(refresh_token, range(2)))

        assert sorted(outcomes) == [
            "compromised",
            "refreshed",
        ]

        auth_session, tokens = load_session_tokens(fixture.session_id)
        original_token = next(token for token in tokens if token.id == fixture.active_token_id)
        replacement_tokens = [token for token in tokens if token.id != fixture.active_token_id]

        assert auth_session.status is AuthSessionStatus.COMPROMISED
        assert auth_session.compromised_at == FIXED_NOW
        assert auth_session.revoked_at is None

        assert original_token.status is RefreshTokenStatus.CONSUMED
        assert original_token.consumed_at == FIXED_NOW
        assert original_token.replaced_by_token_id is not None

        assert len(replacement_tokens) == 1
        assert replacement_tokens[0].status is RefreshTokenStatus.REVOKED
        assert replacement_tokens[0].revoked_at == FIXED_NOW
        assert original_token.replaced_by_token_id == replacement_tokens[0].id
        assert not any(token.status is RefreshTokenStatus.ACTIVE for token in tokens)
    finally:
        delete_committed_fixture(
            fixture.user_id,
            fixture.session_id,
        )


def test_current_rotation_and_old_token_replay_compromise_family() -> None:
    fixture = persist_rotated_session_fixture()
    barrier = Barrier(2)

    def rotate_current_token() -> str:
        service = build_refresh_service(build_generated_token("c"))

        with Session(get_engine()) as session:
            barrier.wait(timeout=10)

            try:
                result = service.execute(
                    session,
                    RefreshAuthenticationCommand(refresh_token=fixture.active_plaintext),
                )
                session.commit()
            except AuthenticationSessionInactiveError:
                session.rollback()
                return "inactive"

        assert isinstance(result, RefreshedAuthentication)
        return "refreshed"

    def replay_consumed_token() -> str:
        service = build_refresh_service(build_generated_token("d"))

        with Session(get_engine()) as session:
            barrier.wait(timeout=10)
            result = service.execute(
                session,
                RefreshAuthenticationCommand(refresh_token=fixture.consumed_plaintext),
            )
            session.commit()

        assert isinstance(
            result,
            CompromisedAuthenticationSession,
        )
        return "compromised"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            rotation_future = executor.submit(rotate_current_token)
            replay_future = executor.submit(replay_consumed_token)

            rotation_outcome = rotation_future.result(timeout=20)
            replay_outcome = replay_future.result(timeout=20)

        assert rotation_outcome in {"refreshed", "inactive"}
        assert replay_outcome == "compromised"

        auth_session, tokens = load_session_tokens(fixture.session_id)
        consumed_token = next(token for token in tokens if token.id == fixture.consumed_token_id)
        previous_active_token = next(
            token for token in tokens if token.id == fixture.active_token_id
        )

        assert auth_session.status is AuthSessionStatus.COMPROMISED
        assert auth_session.compromised_at == FIXED_NOW
        assert consumed_token.status is RefreshTokenStatus.CONSUMED
        assert not any(token.status is RefreshTokenStatus.ACTIVE for token in tokens)

        if rotation_outcome == "refreshed":
            assert len(tokens) == 3
            assert previous_active_token.status is RefreshTokenStatus.CONSUMED
            newest_token = next(
                token
                for token in tokens
                if token.id
                not in {
                    fixture.consumed_token_id,
                    fixture.active_token_id,
                }
            )
            assert newest_token.status is RefreshTokenStatus.REVOKED
            assert newest_token.revoked_at == FIXED_NOW
            assert previous_active_token.replaced_by_token_id == newest_token.id
        else:
            assert len(tokens) == 2
            assert previous_active_token.status is RefreshTokenStatus.REVOKED
            assert previous_active_token.revoked_at == FIXED_NOW
    finally:
        delete_committed_fixture(
            fixture.user_id,
            fixture.session_id,
        )


def test_refresh_and_logout_leave_session_revoked() -> None:
    fixture = persist_active_session_fixture()
    barrier = Barrier(2)

    def refresh_token() -> str:
        service = build_refresh_service(build_generated_token("b"))

        with Session(get_engine()) as session:
            barrier.wait(timeout=10)

            try:
                result = service.execute(
                    session,
                    RefreshAuthenticationCommand(refresh_token=fixture.active_plaintext),
                )
                session.commit()
            except AuthenticationSessionInactiveError:
                session.rollback()
                return "inactive"

        assert isinstance(result, RefreshedAuthentication)
        return "refreshed"

    def revoke_session() -> str:
        with Session(get_engine()) as session:
            barrier.wait(timeout=10)
            build_revocation_service().execute(
                session,
                RevokeAuthenticationSessionCommand(
                    user_id=fixture.user_id,
                    session_id=fixture.session_id,
                ),
            )
            session.commit()

        return "revoked"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            refresh_future = executor.submit(refresh_token)
            revocation_future = executor.submit(revoke_session)

            refresh_outcome = refresh_future.result(timeout=20)
            revocation_outcome = revocation_future.result(timeout=20)

        assert refresh_outcome in {"refreshed", "inactive"}
        assert revocation_outcome == "revoked"

        auth_session, tokens = load_session_tokens(fixture.session_id)
        original_token = next(token for token in tokens if token.id == fixture.active_token_id)

        assert auth_session.status is AuthSessionStatus.REVOKED
        assert auth_session.revoked_at == FIXED_NOW
        assert auth_session.compromised_at is None
        assert not any(token.status is RefreshTokenStatus.ACTIVE for token in tokens)

        if refresh_outcome == "refreshed":
            assert len(tokens) == 2
            assert original_token.status is RefreshTokenStatus.CONSUMED
            replacement = next(token for token in tokens if token.id != fixture.active_token_id)
            assert replacement.status is RefreshTokenStatus.REVOKED
            assert replacement.revoked_at == FIXED_NOW
        else:
            assert len(tokens) == 1
            assert original_token.status is RefreshTokenStatus.REVOKED
            assert original_token.revoked_at == FIXED_NOW
    finally:
        delete_committed_fixture(
            fixture.user_id,
            fixture.session_id,
        )


def test_database_rejects_concurrent_active_refresh_tokens() -> None:
    fixture = persist_session_without_token_fixture()
    barrier = Barrier(2)

    def insert_active_token(worker_id: int) -> str:
        token_id = uuid4()
        plaintext = build_plaintext_token(
            token_id,
            "a" if worker_id == 0 else "b",
        )
        refresh_token = RefreshToken(
            id=token_id,
            session_id=fixture.session_id,
            token_digest=digest_refresh_token(plaintext),
            expires_at=SESSION_EXPIRES_AT,
            created_at=SESSION_CREATED_AT,
        )

        with Session(get_engine()) as session:
            session.add(refresh_token)
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
                assert original_exception.diag.constraint_name == ACTIVE_REFRESH_TOKEN_UNIQUE_INDEX
                return "unique_violation"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(insert_active_token, range(2)))

        assert sorted(outcomes) == [
            "inserted",
            "unique_violation",
        ]

        with Session(get_engine()) as verification_session:
            active_token_count = verification_session.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .where(
                    RefreshToken.session_id == fixture.session_id,
                    RefreshToken.status == RefreshTokenStatus.ACTIVE,
                )
            )

        assert active_token_count == 1
    finally:
        delete_committed_fixture(
            fixture.user_id,
            fixture.session_id,
        )
