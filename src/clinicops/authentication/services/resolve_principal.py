from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.authentication.access_tokens import AccessTokenCodec
from clinicops.authentication.exceptions import (
    AuthenticationSessionExpiredError,
    AuthenticationSessionInactiveError,
    AuthenticationSessionNotFoundError,
)
from clinicops.authentication.models import AuthSessionStatus
from clinicops.authentication.repository import (
    AuthenticationRepository,
)
from clinicops.core.clock import Clock, SystemClock
from clinicops.identity.exceptions import UserDisabledError
from clinicops.identity.models import UserStatus


@dataclass(frozen=True, slots=True)
class ResolveAuthenticatedPrincipalCommand:
    """Access token presented by a protected request."""

    access_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Trusted global identity established from current state."""

    user_id: UUID
    session_id: UUID
    access_token_id: UUID
    authenticated_at: datetime
    access_token_expires_at: datetime
    session_expires_at: datetime


class ResolveAuthenticatedPrincipalService:
    """Resolve a trusted principal without locking database rows."""

    def __init__(
        self,
        access_token_codec: AccessTokenCodec,
        authentication_repository: AuthenticationRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._access_token_codec = access_token_codec
        self._authentication_repository = (
            authentication_repository
            if authentication_repository is not None
            else AuthenticationRepository()
        )
        self._clock = clock if clock is not None else SystemClock()

    def execute(
        self,
        session: Session,
        command: ResolveAuthenticatedPrincipalCommand,
    ) -> AuthenticatedPrincipal:
        """Validate token claims against current persisted state."""

        now = self._clock.now()
        claims = self._access_token_codec.decode(
            command.access_token,
            now=now,
        )
        principal_state = self._authentication_repository.get_authenticated_principal_state(
            session,
            user_id=claims.user_id,
            session_id=claims.session_id,
        )

        if principal_state is None:
            raise AuthenticationSessionNotFoundError()

        auth_session = principal_state.auth_session
        user = principal_state.user

        if auth_session.status is not AuthSessionStatus.ACTIVE:
            raise AuthenticationSessionInactiveError()

        if now >= auth_session.expires_at:
            raise AuthenticationSessionExpiredError()

        if user.status is not UserStatus.ACTIVE:
            raise UserDisabledError()

        return AuthenticatedPrincipal(
            user_id=user.id,
            session_id=auth_session.id,
            access_token_id=claims.token_id,
            authenticated_at=claims.issued_at,
            access_token_expires_at=claims.expires_at,
            session_expires_at=auth_session.expires_at,
        )
