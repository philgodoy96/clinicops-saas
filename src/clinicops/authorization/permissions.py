from enum import StrEnum


class TenantPermission(StrEnum):
    """Stable tenant-scoped capabilities used by application services."""

    TENANT_READ = "tenant:read"
    MEMBER_READ = "member:read"
    MEMBER_INVITE = "member:invite"
    MEMBER_MANAGE = "member:manage"
    INVITATION_READ = "invitation:read"
    INVITATION_CREATE = "invitation:create"
    INVITATION_REVOKE = "invitation:revoke"
    BILLING_READ = "billing:read"
    BILLING_MANAGE = "billing:manage"
    AUDIT_LOG_READ = "audit_log:read"
