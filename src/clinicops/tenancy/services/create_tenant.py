from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.identity.exceptions import (
    UserDisabledError,
    UserNotFoundError,
)
from clinicops.identity.models import UserStatus
from clinicops.identity.repository import UserRepository
from clinicops.tenancy.models import (
    Membership,
    Tenant,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.names import normalize_tenant_name
from clinicops.tenancy.repository import TenantRepository


@dataclass(frozen=True, slots=True)
class CreateTenantCommand:
    """Input required to create a tenant with its initial owner."""

    name: str
    owner_user_id: UUID
    audit_context: AuditRecordingContext


@dataclass(frozen=True, slots=True)
class CreatedTenant:
    """Public application result of tenant creation."""

    id: UUID
    name: str
    status: TenantStatus
    owner_user_id: UUID
    created_at: datetime


class CreateTenantService:
    """Create a tenant and its initial owner in one transaction."""

    def __init__(
        self,
        user_repository: UserRepository | None = None,
        tenant_repository: TenantRepository | None = None,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._user_repository = user_repository if user_repository is not None else UserRepository()
        self._tenant_repository = (
            tenant_repository if tenant_repository is not None else TenantRepository()
        )
        self._audit_recorder = (
            audit_recorder if audit_recorder is not None else SqlAlchemyAuditRecorder()
        )

    def execute(
        self,
        session: Session,
        command: CreateTenantCommand,
    ) -> CreatedTenant:
        """Create a tenant without committing the transaction."""

        tenant_name = normalize_tenant_name(command.name)
        owner_user = self._user_repository.get_by_id_for_update(
            session,
            command.owner_user_id,
        )

        if owner_user is None:
            raise UserNotFoundError()

        if owner_user.status != UserStatus.ACTIVE:
            raise UserDisabledError()

        tenant = Tenant(name=tenant_name)
        tenant.memberships.append(
            Membership(
                user=owner_user,
                role=TenantRole.OWNER,
            )
        )

        self._tenant_repository.add_and_flush(session, tenant)

        audit_context = command.audit_context
        self._audit_recorder.record(
            session,
            RecordAuditLogCommand(
                tenant_id=tenant.id,
                actor=audit_context.actor,
                source=audit_context.source,
                action=AuditAction.TENANT_CREATED.value,
                resource_type=AuditResourceType.TENANT.value,
                resource_id=str(tenant.id),
                correlation_id=audit_context.correlation_id,
                metadata_version=1,
                metadata={
                    "tenant_name": tenant.name,
                },
                idempotency_key=f"tenant-created:{tenant.id}",
                request_id=audit_context.request_id,
            ),
        )

        return CreatedTenant(
            id=tenant.id,
            name=tenant.name,
            status=tenant.status,
            owner_user_id=owner_user.id,
            created_at=tenant.created_at,
        )
