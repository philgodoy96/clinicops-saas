import pytest

from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.policy import (
    ROLE_PERMISSIONS,
    permissions_for_role,
    role_has_permission,
)
from clinicops.tenancy.models import TenantRole

EXPECTED_OWNER_PERMISSIONS = frozenset(TenantPermission)

EXPECTED_ADMIN_PERMISSIONS = frozenset(
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

EXPECTED_STAFF_PERMISSIONS = frozenset(
    {
        TenantPermission.TENANT_READ,
    }
)


def test_policy_defines_every_tenant_role() -> None:
    assert frozenset(ROLE_PERMISSIONS) == frozenset(TenantRole)


@pytest.mark.parametrize(
    ("role", "expected_permissions"),
    [
        (TenantRole.OWNER, EXPECTED_OWNER_PERMISSIONS),
        (TenantRole.ADMIN, EXPECTED_ADMIN_PERMISSIONS),
        (TenantRole.STAFF, EXPECTED_STAFF_PERMISSIONS),
    ],
)
def test_permissions_for_role_returns_exact_policy(
    role: TenantRole,
    expected_permissions: frozenset[TenantPermission],
) -> None:
    permissions = permissions_for_role(role)

    assert permissions == expected_permissions
    assert isinstance(permissions, frozenset)


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (TenantRole.OWNER, TenantPermission.BILLING_MANAGE),
        (TenantRole.OWNER, TenantPermission.MEMBER_MANAGE),
        (TenantRole.ADMIN, TenantPermission.MEMBER_INVITE),
        (TenantRole.ADMIN, TenantPermission.BILLING_READ),
        (TenantRole.STAFF, TenantPermission.TENANT_READ),
    ],
)
def test_role_has_permission_returns_true_for_granted_capability(
    role: TenantRole,
    permission: TenantPermission,
) -> None:
    assert role_has_permission(role, permission) is True


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (TenantRole.ADMIN, TenantPermission.BILLING_MANAGE),
        (TenantRole.STAFF, TenantPermission.MEMBER_READ),
        (TenantRole.STAFF, TenantPermission.MEMBER_INVITE),
        (TenantRole.STAFF, TenantPermission.INVITATION_CREATE),
        (TenantRole.STAFF, TenantPermission.AUDIT_LOG_READ),
    ],
)
def test_role_has_permission_returns_false_for_denied_capability(
    role: TenantRole,
    permission: TenantPermission,
) -> None:
    assert role_has_permission(role, permission) is False


def test_owner_receives_every_declared_permission() -> None:
    for permission in TenantPermission:
        assert (
            role_has_permission(
                TenantRole.OWNER,
                permission,
            )
            is True
        )


def test_admin_cannot_manage_billing() -> None:
    assert (
        role_has_permission(
            TenantRole.ADMIN,
            TenantPermission.BILLING_MANAGE,
        )
        is False
    )


def test_staff_receives_only_tenant_read() -> None:
    assert permissions_for_role(TenantRole.STAFF) == frozenset({TenantPermission.TENANT_READ})
