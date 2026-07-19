from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.identity.exceptions import EmailAlreadyRegisteredError
from clinicops.identity.models import User

EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"


class UserRepository:
    """Persistence operations for global platform users."""

    def get_by_email(
        self,
        session: Session,
        canonical_email: str,
    ) -> User | None:
        """Return the global user registered with a canonical email."""

        return session.scalar(select(User).where(User.email == canonical_email))

    def get_by_email_for_update(
        self,
        session: Session,
        canonical_email: str,
    ) -> User | None:
        """Return and lock the global user registered with an email."""

        statement = select(User).where(User.email == canonical_email).with_for_update()
        return session.scalar(statement)

    def get_by_id_for_update(
        self,
        session: Session,
        user_id: UUID,
    ) -> User | None:
        """Return and lock a global user for a state-sensitive workflow."""

        statement = select(User).where(User.id == user_id).with_for_update()
        return session.scalar(statement)

    def add_and_flush(
        self,
        session: Session,
        user: User,
    ) -> None:
        """Add a global user and flush the current unit of work."""

        session.add(user)

        try:
            session.flush()
        except IntegrityError as exc:
            if _is_email_unique_violation(exc):
                raise EmailAlreadyRegisteredError() from exc
            raise


def _is_email_unique_violation(exception: IntegrityError) -> bool:
    """Return whether PostgreSQL rejected the global email constraint."""

    original_exception = exception.orig

    return (
        isinstance(original_exception, UniqueViolation)
        and original_exception.diag.constraint_name == EMAIL_UNIQUE_CONSTRAINT
    )
