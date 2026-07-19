from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.core.clock import Clock, SystemClock
from clinicops.identity.email import canonicalize_email
from clinicops.identity.repository import UserRepository
from clinicops.invitations.exceptions import (
    InvitationAlreadyPendingError,
    InvitationIssuerNotAuthorizedError,
    InvitationMembershipAlreadyExistsError,
    InvitationRoleNotAllowedError,
)
from clinicops.invitations.models import Invitation, InvitationStatus
from clinicops.invitations.repository import InvitationRepository
from clinicops.invitations.tokens import (
    InvitationToken,
    generate_invitation_token,
)
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

INVITATION_EXPIRATION = timedelta(days=7)
ALLOWED_INVITATION_ROLES = {
    TenantRole.ADMIN,
    TenantRole.STAFF,
}
AUTHORIZED_ISSUER_ROLES = {
    TenantRole.OWNER,
    TenantRole.ADMIN,
}


@dataclass(frozen=True, slots=True)
class IssueInvitationCommand:
    """Input required to issue a tenant invitation."""

    tenant_id: UUID
    issuer_user_id: UUID
    invited_email: str
    role: TenantRole


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    """Application result containing a one-time invitation secret."""

    id: UUID
    tenant_id: UUID
    invited_email: str
    role: TenantRole
    expires_at: datetime
    token: str = field(repr=False)


class IssueInvitationService:
    """Issue a tenant invitation inside the caller's transaction."""

    def __init__(
        self,
        tenant_repository: TenantRepository | None = None,
        user_repository: UserRepository | None = None,
        invitation_repository: InvitationRepository | None = None,
        clock: Clock | None = None,
        token_factory: Callable[[], InvitationToken] | None = None,
    ) -> None:
        self._tenant_repository = (
            tenant_repository if tenant_repository is not None else TenantRepository()
        )
        self._user_repository = user_repository if user_repository is not None else UserRepository()
        self._invitation_repository = (
            invitation_repository if invitation_repository is not None else InvitationRepository()
        )
        self._clock = clock if clock is not None else SystemClock()
        self._token_factory = (
            token_factory if token_factory is not None else generate_invitation_token
        )

    def execute(
        self,
        session: Session,
        command: IssueInvitationCommand,
    ) -> IssuedInvitation:
        """Issue an invitation without committing the transaction."""

        if command.role not in ALLOWED_INVITATION_ROLES:
            raise InvitationRoleNotAllowedError()

        invited_email = canonicalize_email(command.invited_email)

        tenant = self._tenant_repository.get_by_id_for_update(
            session,
            command.tenant_id,
        )

        if tenant is None:
            raise TenantNotFoundError()

        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantDisabledError()

        issuer_membership = self._tenant_repository.get_membership_for_update(
            session,
            tenant.id,
            command.issuer_user_id,
        )

        if (
            issuer_membership is None
            or issuer_membership.status is not MembershipStatus.ACTIVE
            or issuer_membership.role not in AUTHORIZED_ISSUER_ROLES
        ):
            raise InvitationIssuerNotAuthorizedError()

        invited_user = self._user_repository.get_by_email(
            session,
            invited_email,
        )

        if invited_user is not None:
            existing_membership = self._tenant_repository.get_membership_for_update(
                session,
                tenant.id,
                invited_user.id,
            )

            if existing_membership is not None:
                raise InvitationMembershipAlreadyExistsError()

        now = self._clock.now()
        pending_invitation = self._invitation_repository.get_pending_for_update(
            session,
            tenant.id,
            invited_email,
        )

        if pending_invitation is not None:
            if now < pending_invitation.expires_at:
                raise InvitationAlreadyPendingError()

            pending_invitation.status = InvitationStatus.EXPIRED
            self._invitation_repository.flush(session)

        token = self._token_factory()
        expires_at = now + INVITATION_EXPIRATION
        invitation = Invitation(
            tenant=tenant,
            invited_email=invited_email,
            role=command.role,
            token_digest=token.digest,
            created_by_membership=issuer_membership,
            expires_at=expires_at,
            created_at=now,
        )

        self._invitation_repository.add_and_flush(
            session,
            invitation,
        )

        return IssuedInvitation(
            id=invitation.id,
            tenant_id=tenant.id,
            invited_email=invitation.invited_email,
            role=invitation.role,
            expires_at=invitation.expires_at,
            token=token.plaintext,
        )
