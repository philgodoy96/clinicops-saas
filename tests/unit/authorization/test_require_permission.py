from uuid import uuid4

import pytest

from clinicops.authorization.exceptions import (
    TenantPermissionDeniedError,
)
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    RequireTenantPermissionCommand,
    RequireTenantPermissionService,
)
from clinicops.authorization.services.resolve_tenant_context import (
    TenantContext,
)
from clinicops.tenancy.models import TenantRole


def build_tenant_context(
    role: TenantRole,
) -> TenantContext:
    """Build one trusted tenant context for permission tests."""

    return TenantContext(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=uuid4(),
        membership_id=uuid4(),
        role=role,
    )


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (TenantRole.OWNER, TenantPermission.BILLING_MANAGE),
        (TenantRole.OWNER, TenantPermission.MEMBER_MANAGE),
        (TenantRole.ADMIN, TenantPermission.MEMBER_INVITE),
        (TenantRole.ADMIN, TenantPermission.INVITATION_REVOKE),
        (TenantRole.STAFF, TenantPermission.TENANT_READ),
    ],
)
def test_granted_permission_returns_authorized_context(
    role: TenantRole,
    permission: TenantPermission,
) -> None:
    tenant_context = build_tenant_context(role)

    authorized_context = RequireTenantPermissionService().execute(
        RequireTenantPermissionCommand(
            tenant_context=tenant_context,
            permission=permission,
        )
    )

    assert authorized_context.user_id == tenant_context.user_id
    assert authorized_context.session_id == tenant_context.session_id
    assert authorized_context.tenant_id == tenant_context.tenant_id
    assert authorized_context.membership_id == tenant_context.membership_id
    assert authorized_context.role is role
    assert authorized_context.granted_permission is permission


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
def test_denied_permission_raises_generic_authorization_failure(
    role: TenantRole,
    permission: TenantPermission,
) -> None:
    command = RequireTenantPermissionCommand(
        tenant_context=build_tenant_context(role),
        permission=permission,
    )

    with pytest.raises(TenantPermissionDeniedError) as exception_info:
        RequireTenantPermissionService().execute(command)

    assert exception_info.value.code == "tenant_permission_denied"
    assert exception_info.value.public_message == ("The tenant operation is not permitted.")
