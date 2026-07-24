from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.policy import role_has_permission
from clinicops.professionals.contracts import (
    UnlinkProfessionalForMembershipRemovalCommand,
)
from clinicops.professionals.services.unlink_professional_for_membership_removal import (
    UnlinkProfessionalForMembershipRemovalService,
)
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
        audit_recorder: AuditRecorder | None = None,
        professional_unlink_service: (UnlinkProfessionalForMembershipRemovalService | None) = None,
    ) -> None:
        self._repository = (
            repository if repository is not None else MembershipAdministrationRepository()
        )
        self._audit_recorder = (
            audit_recorder if audit_recorder is not None else SqlAlchemyAuditRecorder()
        )
        self._professional_unlink_service = (
            professional_unlink_service
            if professional_unlink_service is not None
            else UnlinkProfessionalForMembershipRemovalService()
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
        removed_role = target.role
        self._professional_unlink_service.execute(
            session,
            UnlinkProfessionalForMembershipRemovalCommand(
                tenant_id=command.tenant_id,
                membership_id=command.membership_id,
                audit_context=command.audit_context,
            ),
        )
        self._repository.delete_and_flush(
            session,
            target,
        )

        audit_context = command.audit_context
        self._audit_recorder.record(
            session,
            RecordAuditLogCommand(
                tenant_id=result.tenant_id,
                actor=audit_context.actor,
                source=audit_context.source,
                action=AuditAction.MEMBERSHIP_REMOVED.value,
                resource_type=AuditResourceType.MEMBERSHIP.value,
                resource_id=str(result.membership_id),
                correlation_id=audit_context.correlation_id,
                metadata_version=1,
                metadata={
                    "target_user_id": str(result.user_id),
                    "removed_role": removed_role.value,
                },
                request_id=audit_context.request_id,
            ),
        )

        return result
