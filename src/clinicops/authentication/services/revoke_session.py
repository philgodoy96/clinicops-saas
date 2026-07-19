from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.authentication.exceptions import (
    AuthenticationSessionInactiveError,
    AuthenticationSessionNotFoundError,
)
from clinicops.authentication.models import (
    AuthSessionStatus,
    RefreshTokenStatus,
)
from clinicops.authentication.repository import (
    AuthenticationRepository,
)
from clinicops.core.clock import Clock, SystemClock
from clinicops.identity.repository import UserRepository


@dataclass(frozen=True, slots=True)
class RevokeAuthenticationSessionCommand:
    """User-owned authentication session to revoke."""

    user_id: UUID
    session_id: UUID


@dataclass(frozen=True, slots=True)
class RevokedAuthenticationSession:
    """Successful authentication session revocation result."""

    session_id: UUID
    revoked_at: datetime


class RevokeAuthenticationSessionService:
    """Revoke one user-owned session inside the caller's transaction."""

    def __init__(
        self,
        authentication_repository: AuthenticationRepository | None = None,
        user_repository: UserRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._authentication_repository = (
            authentication_repository
            if authentication_repository is not None
            else AuthenticationRepository()
        )
        self._user_repository = user_repository if user_repository is not None else UserRepository()
        self._clock = clock if clock is not None else SystemClock()

    def execute(
        self,
        session: Session,
        command: RevokeAuthenticationSessionCommand,
    ) -> RevokedAuthenticationSession:
        """Revoke a session and its active refresh token."""

        user = self._user_repository.get_by_id_for_update(
            session,
            command.user_id,
        )

        if user is None:
            raise AuthenticationSessionNotFoundError()

        auth_session = self._authentication_repository.get_user_session_by_id_for_update(
            session,
            user.id,
            command.session_id,
        )

        if auth_session is None:
            raise AuthenticationSessionNotFoundError()

        if auth_session.status is not AuthSessionStatus.ACTIVE:
            raise AuthenticationSessionInactiveError()

        active_refresh_token = self._authentication_repository.get_active_refresh_token_for_update(
            session,
            auth_session.id,
        )
        revoked_at = self._clock.now()

        auth_session.status = AuthSessionStatus.REVOKED
        auth_session.revoked_at = revoked_at

        if active_refresh_token is not None:
            active_refresh_token.status = RefreshTokenStatus.REVOKED
            active_refresh_token.revoked_at = revoked_at

        self._authentication_repository.flush(session)

        return RevokedAuthenticationSession(
            session_id=auth_session.id,
            revoked_at=revoked_at,
        )
