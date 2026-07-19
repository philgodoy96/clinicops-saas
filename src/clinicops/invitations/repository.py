from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.invitations.exceptions import (
    InvitationAlreadyPendingError,
)
from clinicops.invitations.models import Invitation, InvitationStatus

PENDING_INVITATION_UNIQUE_INDEX = "uq_invitations_one_pending_per_tenant_email"


class InvitationRepository:
    """Persistence operations for tenant invitations."""

    def get_by_digest(
        self,
        session: Session,
        token_digest: str,
    ) -> Invitation | None:
        """Return the invitation identified by a token digest."""

        return session.scalar(select(Invitation).where(Invitation.token_digest == token_digest))

    def get_by_id_for_update(
        self,
        session: Session,
        invitation_id: UUID,
    ) -> Invitation | None:
        """Return and lock an invitation for a lifecycle transition."""

        statement = select(Invitation).where(Invitation.id == invitation_id).with_for_update()
        return session.scalar(statement)

    def get_for_tenant_by_id_for_update(
        self,
        session: Session,
        tenant_id: UUID,
        invitation_id: UUID,
    ) -> Invitation | None:
        """Return and lock an invitation within an explicit tenant."""

        statement = (
            select(Invitation)
            .where(
                Invitation.id == invitation_id,
                Invitation.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        return session.scalar(statement)

    def get_pending_for_update(
        self,
        session: Session,
        tenant_id: UUID,
        invited_email: str,
    ) -> Invitation | None:
        """Return and lock a pending invitation for one tenant email."""

        statement = (
            select(Invitation)
            .where(
                Invitation.tenant_id == tenant_id,
                Invitation.invited_email == invited_email,
                Invitation.status == InvitationStatus.PENDING,
            )
            .with_for_update()
        )
        return session.scalar(statement)

    def add_and_flush(
        self,
        session: Session,
        invitation: Invitation,
    ) -> None:
        """Add an invitation and flush the current unit of work."""

        session.add(invitation)

        try:
            session.flush()
        except IntegrityError as exc:
            if _is_pending_invitation_unique_violation(exc):
                raise InvitationAlreadyPendingError() from exc
            raise

    def flush(self, session: Session) -> None:
        """Flush invitation state without committing the transaction."""

        session.flush()


def _is_pending_invitation_unique_violation(
    exception: IntegrityError,
) -> bool:
    """Return whether PostgreSQL rejected the pending invitation index."""

    original_exception = exception.orig

    return (
        isinstance(original_exception, UniqueViolation)
        and original_exception.diag.constraint_name == PENDING_INVITATION_UNIQUE_INDEX
    )
