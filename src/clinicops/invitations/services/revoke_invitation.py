from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.core.clock import Clock, SystemClock
from clinicops.invitations.exceptions import (
    InvitationActorNotAuthorizedError,
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
)
from clinicops.invitations.models import InvitationStatus
from clinicops.invitations.repository import InvitationRepository
from clinicops.tenancy.exceptions import (
    TenantDisabledError,
    TenantNotFoundError,
)
from clinicops.tenancy.models import (
    MembershipStatus,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.repository import TenantRepository

AUTHORIZED_INVITATION_ACTOR_ROLES = {
    TenantRole.OWNER,
    TenantRole.ADMIN,
}


@dataclass(frozen=True, slots=True)
class RevokeInvitationCommand:
    """Input required to revoke a tenant invitation."""

    tenant_id: UUID
    invitation_id: UUID
    actor_user_id: UUID
    audit_context: AuditRecordingContext


@dataclass(frozen=True, slots=True)
class RevokedInvitation:
    """Public application result of invitation revocation."""

    invitation_id: UUID
    tenant_id: UUID
    revoked_at: datetime


class RevokeInvitationService:
    """Revoke a pending invitation inside the caller's transaction."""

    def __init__(
        self,
        tenant_repository: TenantRepository | None = None,
        invitation_repository: InvitationRepository | None = None,
        clock: Clock | None = None,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._tenant_repository = (
            tenant_repository if tenant_repository is not None else TenantRepository()
        )
        self._invitation_repository = (
            invitation_repository if invitation_repository is not None else InvitationRepository()
        )
        self._clock = clock if clock is not None else SystemClock()
        self._audit_recorder = (
            audit_recorder if audit_recorder is not None else SqlAlchemyAuditRecorder()
        )

    def execute(
        self,
        session: Session,
        command: RevokeInvitationCommand,
    ) -> RevokedInvitation:
        """Revoke an invitation without committing the transaction."""

        tenant = self._tenant_repository.get_by_id_for_update(
            session,
            command.tenant_id,
        )

        if tenant is None:
            raise TenantNotFoundError()

        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantDisabledError()

        actor_membership = self._tenant_repository.get_membership_for_update(
            session,
            tenant.id,
            command.actor_user_id,
        )

        if (
            actor_membership is None
            or actor_membership.status is not MembershipStatus.ACTIVE
            or actor_membership.role not in AUTHORIZED_INVITATION_ACTOR_ROLES
        ):
            raise InvitationActorNotAuthorizedError()

        invitation = self._invitation_repository.get_for_tenant_by_id_for_update(
            session,
            tenant.id,
            command.invitation_id,
        )

        if invitation is None:
            raise InvitationNotFoundError()

        if invitation.status is InvitationStatus.ACCEPTED:
            raise InvitationAlreadyAcceptedError()

        if invitation.status is InvitationStatus.REVOKED:
            raise InvitationRevokedError()

        if invitation.status is InvitationStatus.EXPIRED:
            raise InvitationExpiredError()

        revoked_at = self._clock.now()

        if revoked_at >= invitation.expires_at:
            raise InvitationExpiredError()

        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = revoked_at
        self._invitation_repository.flush(session)

        audit_context = command.audit_context
        self._audit_recorder.record(
            session,
            RecordAuditLogCommand(
                tenant_id=invitation.tenant_id,
                actor=audit_context.actor,
                source=audit_context.source,
                action=AuditAction.INVITATION_REVOKED.value,
                resource_type=AuditResourceType.INVITATION.value,
                resource_id=str(invitation.id),
                correlation_id=audit_context.correlation_id,
                metadata_version=1,
                metadata={
                    "invited_role": invitation.role.value,
                },
                idempotency_key=f"invitation-revoked:{invitation.id}",
                request_id=audit_context.request_id,
            ),
        )

        return RevokedInvitation(
            invitation_id=invitation.id,
            tenant_id=tenant.id,
            revoked_at=revoked_at,
        )
