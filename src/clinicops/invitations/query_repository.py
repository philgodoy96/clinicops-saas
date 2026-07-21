from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from clinicops.invitations.models import Invitation


class InvitationQueryRepository:
    """Read-only invitation persistence queries."""

    def list_for_tenant(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> list[Invitation]:
        """Return invitations owned by one tenant."""

        statement = (
            select(Invitation)
            .where(Invitation.tenant_id == tenant_id)
            .order_by(
                Invitation.created_at.desc(),
                Invitation.id.desc(),
            )
        )

        return list(session.scalars(statement).all())
