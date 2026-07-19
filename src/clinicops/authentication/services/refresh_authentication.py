from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.authentication.access_tokens import AccessTokenCodec
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
    generate_refresh_token,
    parse_refresh_token,
    refresh_token_matches,
)
from clinicops.authentication.repository import (
    AuthenticationRepository,
)
from clinicops.core.clock import Clock, SystemClock
from clinicops.identity.exceptions import UserDisabledError
from clinicops.identity.models import UserStatus
from clinicops.identity.repository import UserRepository


class AuthenticationCompromiseReason(StrEnum):
    """Security reason that compromised an authentication session."""

    REFRESH_TOKEN_REUSE = "refresh_token_reuse"


@dataclass(frozen=True, slots=True)
class RefreshAuthenticationCommand:
    """Bearer token required to rotate authentication credentials."""

    refresh_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RefreshedAuthentication:
    """Successful refresh rotation result with new bearer tokens."""

    user_id: UUID
    session_id: UUID
    access_token: str = field(repr=False)
    access_token_expires_at: datetime
    refresh_token: str = field(repr=False)
    session_expires_at: datetime


@dataclass(frozen=True, slots=True)
class CompromisedAuthenticationSession:
    """Committed security transition caused by refresh token reuse."""

    session_id: UUID
    compromised_at: datetime
    reason: AuthenticationCompromiseReason


RefreshAuthenticationResult = RefreshedAuthentication | CompromisedAuthenticationSession


class RefreshAuthenticationService:
    """Rotate or compromise a session inside the caller's transaction."""

    def __init__(
        self,
        access_token_codec: AccessTokenCodec,
        authentication_repository: AuthenticationRepository | None = None,
        user_repository: UserRepository | None = None,
        clock: Clock | None = None,
        refresh_token_factory: (Callable[[], GeneratedRefreshToken] | None) = None,
    ) -> None:
        self._access_token_codec = access_token_codec
        self._authentication_repository = (
            authentication_repository
            if authentication_repository is not None
            else AuthenticationRepository()
        )
        self._user_repository = user_repository if user_repository is not None else UserRepository()
        self._clock = clock if clock is not None else SystemClock()
        self._refresh_token_factory = (
            refresh_token_factory if refresh_token_factory is not None else generate_refresh_token
        )

    def execute(
        self,
        session: Session,
        command: RefreshAuthenticationCommand,
    ) -> RefreshAuthenticationResult:
        """Rotate a refresh token or record detected token reuse."""

        parsed_token = parse_refresh_token(command.refresh_token)
        metadata = self._authentication_repository.get_refresh_token_metadata(
            session,
            parsed_token.token_id,
        )

        if metadata is None:
            raise RefreshTokenInvalidError()

        user = self._user_repository.get_by_id_for_update(
            session,
            metadata.user_id,
        )
        auth_session = self._authentication_repository.get_session_by_id_for_update(
            session,
            metadata.session_id,
        )
        refresh_token = self._authentication_repository.get_refresh_token_by_id_for_update(
            session,
            metadata.token_id,
        )

        if (
            user is None
            or auth_session is None
            or refresh_token is None
            or auth_session.user_id != user.id
            or refresh_token.session_id != auth_session.id
        ):
            raise RefreshTokenInvalidError()

        if not refresh_token_matches(
            command.refresh_token,
            refresh_token.token_digest,
        ):
            raise RefreshTokenInvalidError()

        now = self._clock.now()

        if user.status is not UserStatus.ACTIVE:
            raise UserDisabledError()

        if auth_session.status is not AuthSessionStatus.ACTIVE:
            raise AuthenticationSessionInactiveError()

        if now >= auth_session.expires_at or now >= refresh_token.expires_at:
            raise RefreshTokenExpiredError()

        if refresh_token.status is RefreshTokenStatus.CONSUMED:
            return self._compromise_reused_session(
                session,
                auth_session,
                now,
            )

        if refresh_token.status is not RefreshTokenStatus.ACTIVE:
            raise RefreshTokenInvalidError()

        return self._rotate_active_token(
            session,
            user.id,
            auth_session,
            refresh_token,
            now,
        )

    def _rotate_active_token(
        self,
        session: Session,
        user_id: UUID,
        auth_session: AuthSession,
        current_token: RefreshToken,
        rotated_at: datetime,
    ) -> RefreshedAuthentication:
        """Consume one token and create its active replacement."""

        replacement = self._refresh_token_factory()

        current_token.status = RefreshTokenStatus.CONSUMED
        current_token.consumed_at = rotated_at
        current_token.replaced_by_token_id = replacement.token_id
        auth_session.last_rotated_at = rotated_at

        self._authentication_repository.flush(session)

        replacement_token = RefreshToken(
            id=replacement.token_id,
            session_id=auth_session.id,
            token_digest=replacement.digest,
            expires_at=auth_session.expires_at,
            created_at=rotated_at,
        )
        self._authentication_repository.add_refresh_token_and_flush(
            session,
            replacement_token,
        )

        issued_access_token = self._access_token_codec.issue(
            user_id=user_id,
            session_id=auth_session.id,
            issued_at=rotated_at,
        )

        return RefreshedAuthentication(
            user_id=user_id,
            session_id=auth_session.id,
            access_token=issued_access_token.token,
            access_token_expires_at=(issued_access_token.expires_at),
            refresh_token=replacement.plaintext,
            session_expires_at=auth_session.expires_at,
        )

    def _compromise_reused_session(
        self,
        session: Session,
        auth_session: AuthSession,
        compromised_at: datetime,
    ) -> CompromisedAuthenticationSession:
        """Compromise a session and revoke its current active token."""

        active_token = self._authentication_repository.get_active_refresh_token_for_update(
            session,
            auth_session.id,
        )

        auth_session.status = AuthSessionStatus.COMPROMISED
        auth_session.compromised_at = compromised_at

        if active_token is not None:
            active_token.status = RefreshTokenStatus.REVOKED
            active_token.revoked_at = compromised_at

        self._authentication_repository.flush(session)

        return CompromisedAuthenticationSession(
            session_id=auth_session.id,
            compromised_at=compromised_at,
            reason=(AuthenticationCompromiseReason.REFRESH_TOKEN_REUSE),
        )
