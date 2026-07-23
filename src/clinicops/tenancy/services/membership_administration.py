from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from clinicops.audit.context import AuditRecordingContext
from clinicops.tenancy.models import TenantRole


@dataclass(frozen=True, slots=True)
class ChangeMembershipRoleCommand:
    """Input required to change a tenant membership role."""

    tenant_id: UUID
    actor_user_id: UUID
    membership_id: UUID
    role: TenantRole
    audit_context: AuditRecordingContext


@dataclass(frozen=True, slots=True)
class ChangedMembershipRole:
    """Public application result of a membership role change."""

    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    previous_role: TenantRole
    role: TenantRole
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DisableMembershipCommand:
    """Input required to disable a tenant membership."""

    tenant_id: UUID
    actor_user_id: UUID
    membership_id: UUID


@dataclass(frozen=True, slots=True)
class DisabledMembership:
    """Public application result of disabling a membership."""

    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    disabled_at: datetime


@dataclass(frozen=True, slots=True)
class EnableMembershipCommand:
    """Input required to enable a tenant membership."""

    tenant_id: UUID
    actor_user_id: UUID
    membership_id: UUID


@dataclass(frozen=True, slots=True)
class EnabledMembership:
    """Public application result of enabling a membership."""

    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RemoveMembershipCommand:
    """Input required to remove a tenant membership."""

    tenant_id: UUID
    actor_user_id: UUID
    membership_id: UUID
    audit_context: AuditRecordingContext


@dataclass(frozen=True, slots=True)
class RemovedMembership:
    """Application result of permanently removing a membership."""

    membership_id: UUID
    tenant_id: UUID
    user_id: UUID
