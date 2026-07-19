from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from clinicops.authentication.access_tokens import AccessTokenCodec
from clinicops.authentication.config import (
    AUTHENTICATION_SESSION_LIFETIME,
)
from clinicops.authentication.exceptions import InvalidCredentialsError
from clinicops.authentication.models import AuthSession, RefreshToken
from clinicops.authentication.refresh_tokens import (
    GeneratedRefreshToken,
    generate_refresh_token,
)
from clinicops.authentication.repository import (
    AuthenticationRepository,
)
from clinicops.core.clock import Clock, SystemClock
from clinicops.identity.email import canonicalize_email
from clinicops.identity.models import UserStatus
from clinicops.identity.passwords import (
    Argon2PasswordHasher,
    PasswordHasher,
)
from clinicops.identity.repository import UserRepository

DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "DCZ/3kyGLq0GL21MWiX8tg$"
    "2kJrGg+U95TRjSel8QaqX8yIvncR7yCQ6wGd2F3FYQY"
)


@dataclass(frozen=True, slots=True)
class AuthenticateUserCommand:
    """Credentials required to create a global authentication session."""

    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """Authentication result containing newly issued bearer tokens."""

    user_id: UUID
    session_id: UUID
    access_token: str = field(repr=False)
    access_token_expires_at: datetime
    refresh_token: str = field(repr=False)
    session_expires_at: datetime


class AuthenticateUserService:
    """Authenticate a global user inside the caller's transaction."""

    def __init__(
        self,
        access_token_codec: AccessTokenCodec,
        user_repository: UserRepository | None = None,
        authentication_repository: AuthenticationRepository | None = None,
        password_hasher: PasswordHasher | None = None,
        clock: Clock | None = None,
        refresh_token_factory: (Callable[[], GeneratedRefreshToken] | None) = None,
    ) -> None:
        self._access_token_codec = access_token_codec
        self._user_repository = user_repository if user_repository is not None else UserRepository()
        self._authentication_repository = (
            authentication_repository
            if authentication_repository is not None
            else AuthenticationRepository()
        )
        self._password_hasher = (
            password_hasher if password_hasher is not None else Argon2PasswordHasher()
        )
        self._clock = clock if clock is not None else SystemClock()
        self._refresh_token_factory = (
            refresh_token_factory if refresh_token_factory is not None else generate_refresh_token
        )

    def execute(
        self,
        session: Session,
        command: AuthenticateUserCommand,
    ) -> AuthenticatedSession:
        """Create an authentication session without committing it."""

        canonical_email = canonicalize_email(command.email)
        user = self._user_repository.get_by_email(
            session,
            canonical_email,
        )
        credential = user.password_credential if user is not None else None
        password_hash = credential.password_hash if credential is not None else DUMMY_PASSWORD_HASH
        password_matches = self._password_hasher.verify(
            command.password,
            password_hash,
        )

        if user is None or credential is None or not password_matches:
            raise InvalidCredentialsError()

        locked_user = self._user_repository.get_by_id_for_update(
            session,
            user.id,
        )

        if locked_user is None or locked_user.status is not UserStatus.ACTIVE:
            raise InvalidCredentialsError()

        issued_at = self._clock.now()
        session_expires_at = issued_at + AUTHENTICATION_SESSION_LIFETIME
        session_id = uuid4()
        generated_refresh_token = self._refresh_token_factory()
        auth_session = AuthSession(
            id=session_id,
            user_id=locked_user.id,
            expires_at=session_expires_at,
            last_rotated_at=issued_at,
            created_at=issued_at,
        )
        refresh_token = RefreshToken(
            id=generated_refresh_token.token_id,
            auth_session=auth_session,
            token_digest=generated_refresh_token.digest,
            expires_at=session_expires_at,
            created_at=issued_at,
        )

        self._authentication_repository.add_session_and_refresh_token_and_flush(
            session,
            auth_session,
            refresh_token,
        )
        issued_access_token = self._access_token_codec.issue(
            user_id=locked_user.id,
            session_id=auth_session.id,
            issued_at=issued_at,
        )

        return AuthenticatedSession(
            user_id=locked_user.id,
            session_id=auth_session.id,
            access_token=issued_access_token.token,
            access_token_expires_at=(issued_access_token.expires_at),
            refresh_token=generated_refresh_token.plaintext,
            session_expires_at=auth_session.expires_at,
        )
