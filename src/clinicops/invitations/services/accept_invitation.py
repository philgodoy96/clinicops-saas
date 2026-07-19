from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.core.clock import Clock, SystemClock
from clinicops.identity.exceptions import UserDisabledError
from clinicops.identity.models import UserStatus
from clinicops.identity.repository import UserRepository
from clinicops.identity.service import (
    CreateUserCommand,
    CreateUserService,
)
from clinicops.invitations.exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationMembershipAlreadyExistsError,
    InvitationPasswordRequiredError,
    InvitationRevokedError,
    InvitationTokenInvalidError,
)
from clinicops.invitations.models import InvitationStatus
from clinicops.invitations.repository import InvitationRepository
from clinicops.invitations.tokens import digest_invitation_token
from clinicops.tenancy.exceptions import (
    TenantDisabledError,
    TenantNotFoundError,
)
from clinicops.tenancy.models import Membership, TenantRole, TenantStatus
from clinicops.tenancy.repository import TenantRepository


@dataclass(frozen=True, slots=True)
class AcceptInvitationCommand:
    """Input required to accept a tenant invitation."""

    token: str = field(repr=False)
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AcceptedInvitation:
    """Public application result of invitation acceptance."""

    invitation_id: UUID
    tenant_id: UUID
    membership_id: UUID
    user_id: UUID
    role: TenantRole
    user_was_created: bool
    accepted_at: datetime


class AcceptInvitationService:
    """Accept an invitation inside the caller's transaction."""

    def __init__(
        self,
        invitation_repository: InvitationRepository | None = None,
        tenant_repository: TenantRepository | None = None,
        user_repository: UserRepository | None = None,
        create_user_service: CreateUserService | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._invitation_repository = (
            invitation_repository if invitation_repository is not None else InvitationRepository()
        )
        self._tenant_repository = (
            tenant_repository if tenant_repository is not None else TenantRepository()
        )
        self._user_repository = user_repository if user_repository is not None else UserRepository()
        self._create_user_service = (
            create_user_service if create_user_service is not None else CreateUserService()
        )
        self._clock = clock if clock is not None else SystemClock()

    def execute(
        self,
        session: Session,
        command: AcceptInvitationCommand,
    ) -> AcceptedInvitation:
        """Accept an invitation without committing the transaction."""

        token_digest = digest_invitation_token(command.token)
        resolved_invitation = self._invitation_repository.get_by_digest(
            session,
            token_digest,
        )

        if resolved_invitation is None:
            raise InvitationTokenInvalidError()

        tenant = self._tenant_repository.get_by_id_for_update(
            session,
            resolved_invitation.tenant_id,
        )

        if tenant is None:
            raise TenantNotFoundError()

        invitation = self._invitation_repository.get_by_id_for_update(
            session,
            resolved_invitation.id,
        )

        if invitation is None or invitation.token_digest != token_digest:
            raise InvitationTokenInvalidError()

        if invitation.status is InvitationStatus.ACCEPTED:
            raise InvitationAlreadyAcceptedError()

        if invitation.status is InvitationStatus.REVOKED:
            raise InvitationRevokedError()

        if invitation.status is InvitationStatus.EXPIRED:
            raise InvitationExpiredError()

        accepted_at = self._clock.now()

        if accepted_at >= invitation.expires_at:
            raise InvitationExpiredError()

        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantDisabledError()

        invited_user = self._user_repository.get_by_email_for_update(
            session,
            invitation.invited_email,
        )
        user_was_created = False

        if invited_user is None:
            if command.password is None:
                raise InvitationPasswordRequiredError()

            created_user = self._create_user_service.execute(
                session,
                CreateUserCommand(
                    email=invitation.invited_email,
                    password=command.password,
                ),
            )
            user_id = created_user.id
            user_was_created = True
        else:
            if invited_user.status is not UserStatus.ACTIVE:
                raise UserDisabledError()

            user_id = invited_user.id

        existing_membership = self._tenant_repository.get_membership_for_update(
            session,
            tenant.id,
            user_id,
        )

        if existing_membership is not None:
            raise InvitationMembershipAlreadyExistsError()

        membership = Membership(
            tenant_id=tenant.id,
            user_id=user_id,
            role=invitation.role,
        )
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_by_user_id = user_id
        invitation.accepted_at = accepted_at

        self._tenant_repository.add_membership_and_flush(
            session,
            membership,
        )

        return AcceptedInvitation(
            invitation_id=invitation.id,
            tenant_id=tenant.id,
            membership_id=membership.id,
            user_id=user_id,
            role=invitation.role,
            user_was_created=user_was_created,
            accepted_at=accepted_at,
        )
