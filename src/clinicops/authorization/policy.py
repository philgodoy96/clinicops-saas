from types import MappingProxyType
from typing import Final

from clinicops.authorization.permissions import TenantPermission
from clinicops.tenancy.models import TenantRole

_OWNER_PERMISSIONS = frozenset(TenantPermission)

_ADMIN_PERMISSIONS = frozenset(
    {
        TenantPermission.TENANT_READ,
        TenantPermission.MEMBER_READ,
        TenantPermission.MEMBER_INVITE,
        TenantPermission.MEMBER_MANAGE,
        TenantPermission.INVITATION_READ,
        TenantPermission.INVITATION_CREATE,
        TenantPermission.INVITATION_REVOKE,
        TenantPermission.BILLING_READ,
        TenantPermission.AUDIT_LOG_READ,
    }
)

_STAFF_PERMISSIONS = frozenset(
    {
        TenantPermission.TENANT_READ,
    }
)

ROLE_PERMISSIONS: Final = MappingProxyType(
    {
        TenantRole.OWNER: _OWNER_PERMISSIONS,
        TenantRole.ADMIN: _ADMIN_PERMISSIONS,
        TenantRole.STAFF: _STAFF_PERMISSIONS,
    }
)


def permissions_for_role(
    role: TenantRole,
) -> frozenset[TenantPermission]:
    """Return the immutable permission set granted to a tenant role."""

    return ROLE_PERMISSIONS[role]


def role_has_permission(
    role: TenantRole,
    permission: TenantPermission,
) -> bool:
    """Return whether a tenant role grants one permission."""

    return permission in permissions_for_role(role)
