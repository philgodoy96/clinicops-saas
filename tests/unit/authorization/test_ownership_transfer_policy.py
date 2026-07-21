import pytest

from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.policy import role_has_permission
from clinicops.tenancy.models import TenantRole


def test_ownership_transfer_permission_has_stable_value() -> None:
    assert TenantPermission.OWNERSHIP_TRANSFER.value == "ownership:transfer"


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (TenantRole.OWNER, True),
        (TenantRole.ADMIN, False),
        (TenantRole.STAFF, False),
    ],
)
def test_ownership_transfer_permission_is_owner_only(
    role: TenantRole,
    expected: bool,
) -> None:
    assert (
        role_has_permission(
            role,
            TenantPermission.OWNERSHIP_TRANSFER,
        )
        is expected
    )
