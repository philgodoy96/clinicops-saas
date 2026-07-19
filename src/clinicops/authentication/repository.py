from sqlalchemy.orm import Session

from clinicops.authentication.models import AuthSession, RefreshToken


class AuthenticationRepository:
    """Persistence operations for global authentication sessions."""

    def add_session_and_refresh_token_and_flush(
        self,
        session: Session,
        auth_session: AuthSession,
        refresh_token: RefreshToken,
    ) -> None:
        """Persist a session and its initial refresh token."""

        session.add_all([auth_session, refresh_token])
        session.flush()
