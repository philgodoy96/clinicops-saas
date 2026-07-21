from sqlalchemy.orm import Session

from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.policy import role_has_permission
from clinicops.core.clock import Clock, SystemClock
from clinicops.tenancy.exceptions import (
    MembershipActorNotAuthorizedError,
    MembershipAlreadyDisabledError,
    MembershipNotFoundError,
    MembershipOwnerProtectedError,
    MembershipSelfManagementNotAllowedError,
    TenantDisabledError,
    TenantNotFoundError,
)
from clinicops.tenancy.membership_administration_repository import (
    MembershipAdministrationRepository,
)
from clinicops.tenancy.models import (
    MembershipStatus,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.services.membership_administration import (
    DisabledMembership,
    DisableMembershipCommand,
)


class DisableMembershipService:
    """Disable a non-owner membership inside one transaction."""

    def __init__(
        self,
        repository: MembershipAdministrationRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._repository = (
            repository if repository is not None else MembershipAdministrationRepository()
        )
        self._clock = clock if clock is not None else SystemClock()

    def execute(
        self,
        session: Session,
        command: DisableMembershipCommand,
    ) -> DisabledMembership:
        """Disable a membership without committing."""

        tenant = self._repository.get_tenant_for_update(
            session,
            command.tenant_id,
        )

        if tenant is None:
            raise TenantNotFoundError()

        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantDisabledError()

        state = self._repository.get_actor_and_target_for_update(
            session,
            tenant_id=tenant.id,
            actor_user_id=command.actor_user_id,
            target_membership_id=command.membership_id,
        )
        actor = state.actor

        if (
            actor is None
            or actor.status is not MembershipStatus.ACTIVE
            or not role_has_permission(
                actor.role,
                TenantPermission.MEMBER_MANAGE,
            )
        ):
            raise MembershipActorNotAuthorizedError()

        target = state.target

        if target is None:
            raise MembershipNotFoundError()

        if target.user_id == actor.user_id:
            raise MembershipSelfManagementNotAllowedError()

        if target.role is TenantRole.OWNER:
            raise MembershipOwnerProtectedError()

        if target.status is MembershipStatus.DISABLED:
            raise MembershipAlreadyDisabledError()

        disabled_at = self._clock.now()
        target.status = MembershipStatus.DISABLED
        target.disabled_at = disabled_at
        self._repository.flush_and_refresh(
            session,
            target,
        )

        return DisabledMembership(
            membership_id=target.id,
            tenant_id=target.tenant_id,
            user_id=target.user_id,
            role=target.role,
            disabled_at=disabled_at,
        )
