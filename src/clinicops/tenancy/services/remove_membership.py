from sqlalchemy.orm import Session

from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.policy import role_has_permission
from clinicops.tenancy.exceptions import (
    MembershipActorNotAuthorizedError,
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
    RemovedMembership,
    RemoveMembershipCommand,
)


class RemoveMembershipService:
    """Remove a non-owner tenant membership inside one transaction."""

    def __init__(
        self,
        repository: MembershipAdministrationRepository | None = None,
    ) -> None:
        self._repository = (
            repository if repository is not None else MembershipAdministrationRepository()
        )

    def execute(
        self,
        session: Session,
        command: RemoveMembershipCommand,
    ) -> RemovedMembership:
        """Remove a membership without committing."""

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

        result = RemovedMembership(
            membership_id=target.id,
            tenant_id=target.tenant_id,
            user_id=target.user_id,
        )
        self._repository.delete_and_flush(
            session,
            target,
        )

        return result
