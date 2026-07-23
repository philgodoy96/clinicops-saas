from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.policy import role_has_permission
from clinicops.tenancy.exceptions import (
    MembershipActorNotAuthorizedError,
    MembershipDisabledError,
    MembershipNotFoundError,
    MembershipOwnerProtectedError,
    MembershipRoleNotAllowedError,
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
    ChangedMembershipRole,
    ChangeMembershipRoleCommand,
)


class ChangeMembershipRoleService:
    """Change a non-owner membership role inside one transaction."""

    def __init__(
        self,
        repository: MembershipAdministrationRepository | None = None,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._repository = (
            repository if repository is not None else MembershipAdministrationRepository()
        )
        self._audit_recorder = (
            audit_recorder if audit_recorder is not None else SqlAlchemyAuditRecorder()
        )

    def execute(
        self,
        session: Session,
        command: ChangeMembershipRoleCommand,
    ) -> ChangedMembershipRole:
        """Change a membership role without committing."""

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

        if command.role not in {
            TenantRole.ADMIN,
            TenantRole.STAFF,
        }:
            raise MembershipRoleNotAllowedError()

        if target.role is TenantRole.OWNER:
            raise MembershipOwnerProtectedError()

        if target.status is not MembershipStatus.ACTIVE:
            raise MembershipDisabledError()

        previous_role = target.role

        if previous_role is not command.role:
            target.role = command.role
            self._repository.flush_and_refresh(
                session,
                target,
            )

            audit_context = command.audit_context
            self._audit_recorder.record(
                session,
                RecordAuditLogCommand(
                    tenant_id=target.tenant_id,
                    actor=audit_context.actor,
                    source=audit_context.source,
                    action=AuditAction.MEMBERSHIP_ROLE_CHANGED.value,
                    resource_type=AuditResourceType.MEMBERSHIP.value,
                    resource_id=str(target.id),
                    correlation_id=audit_context.correlation_id,
                    metadata_version=1,
                    metadata={
                        "target_user_id": str(target.user_id),
                        "previous_role": previous_role.value,
                        "new_role": target.role.value,
                    },
                    request_id=audit_context.request_id,
                ),
            )

        return ChangedMembershipRole(
            membership_id=target.id,
            tenant_id=target.tenant_id,
            user_id=target.user_id,
            previous_role=previous_role,
            role=target.role,
            updated_at=target.updated_at,
        )
