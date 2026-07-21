from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.invitations.models import InvitationStatus
from clinicops.invitations.query_repository import (
    InvitationQueryRepository,
)
from clinicops.tenancy.models import TenantRole


@dataclass(frozen=True, slots=True)
class ListTenantInvitationsCommand:
    """Trusted selected tenant used for invitation discovery."""

    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class TenantInvitation:
    """Read-only public invitation details."""

    id: UUID
    tenant_id: UUID
    invited_email: str
    role: TenantRole
    status: InvitationStatus
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ListTenantInvitationsService:
    """List invitations owned by one trusted tenant."""

    def __init__(
        self,
        query_repository: InvitationQueryRepository | None = None,
    ) -> None:
        self._query_repository = (
            query_repository if query_repository is not None else InvitationQueryRepository()
        )

    def execute(
        self,
        session: Session,
        command: ListTenantInvitationsCommand,
    ) -> tuple[TenantInvitation, ...]:
        """Return tenant invitations without exposing secrets."""

        invitations = self._query_repository.list_for_tenant(
            session,
            command.tenant_id,
        )

        return tuple(
            TenantInvitation(
                id=invitation.id,
                tenant_id=invitation.tenant_id,
                invited_email=invitation.invited_email,
                role=invitation.role,
                status=invitation.status,
                expires_at=invitation.expires_at,
                accepted_at=invitation.accepted_at,
                revoked_at=invitation.revoked_at,
                created_at=invitation.created_at,
                updated_at=invitation.updated_at,
            )
            for invitation in invitations
        )
