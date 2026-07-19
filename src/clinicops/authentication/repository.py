from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clinicops.authentication.models import (
    AuthSession,
    RefreshToken,
    RefreshTokenStatus,
)


@dataclass(frozen=True, slots=True)
class RefreshTokenMetadata:
    """Identifiers required to establish the refresh lock order."""

    token_id: UUID
    session_id: UUID
    user_id: UUID


class AuthenticationRepository:
    """Persistence operations for global authentication sessions."""

    def get_refresh_token_metadata(
        self,
        session: Session,
        token_id: UUID,
    ) -> RefreshTokenMetadata | None:
        """Return refresh ownership metadata without loading ORM state."""

        row = session.execute(
            select(
                RefreshToken.id,
                RefreshToken.session_id,
                AuthSession.user_id,
            )
            .join(
                AuthSession,
                AuthSession.id == RefreshToken.session_id,
            )
            .where(RefreshToken.id == token_id)
        ).one_or_none()

        if row is None:
            return None

        return RefreshTokenMetadata(
            token_id=row.id,
            session_id=row.session_id,
            user_id=row.user_id,
        )

    def get_session_by_id_for_update(
        self,
        session: Session,
        session_id: UUID,
    ) -> AuthSession | None:
        """Return, lock, and refresh an authentication session."""

        statement = (
            select(AuthSession)
            .where(AuthSession.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return session.scalar(statement)

    def get_user_session_by_id_for_update(
        self,
        session: Session,
        user_id: UUID,
        session_id: UUID,
    ) -> AuthSession | None:
        """Return and lock a session owned by one global user."""

        statement = (
            select(AuthSession)
            .where(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return session.scalar(statement)

    def get_refresh_token_by_id_for_update(
        self,
        session: Session,
        token_id: UUID,
    ) -> RefreshToken | None:
        """Return, lock, and refresh one persisted refresh token."""

        statement = (
            select(RefreshToken)
            .where(RefreshToken.id == token_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return session.scalar(statement)

    def get_active_refresh_token_for_update(
        self,
        session: Session,
        session_id: UUID,
    ) -> RefreshToken | None:
        """Return, lock, and refresh the session's active token."""

        statement = (
            select(RefreshToken)
            .where(
                RefreshToken.session_id == session_id,
                RefreshToken.status == RefreshTokenStatus.ACTIVE,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return session.scalar(statement)

    def add_session_and_refresh_token_and_flush(
        self,
        session: Session,
        auth_session: AuthSession,
        refresh_token: RefreshToken,
    ) -> None:
        """Persist a session and its initial refresh token."""

        session.add_all([auth_session, refresh_token])
        session.flush()

    def add_refresh_token_and_flush(
        self,
        session: Session,
        refresh_token: RefreshToken,
    ) -> None:
        """Persist one replacement refresh token."""

        session.add(refresh_token)
        session.flush()

    def flush(self, session: Session) -> None:
        """Flush authentication state without committing."""

        session.flush()
