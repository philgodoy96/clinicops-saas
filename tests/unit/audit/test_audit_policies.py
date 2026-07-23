import pytest

from clinicops.audit.policies import (
    AuditLogAccessDeniedError,
    ensure_can_read_audit_logs,
)


@pytest.mark.parametrize(
    "role",
    [
        "OWNER",
        "ADMIN",
        "owner",
        "admin",
        "  OWNER  ",
        "  admin  ",
    ],
)
def test_owner_and_admin_can_read_audit_logs(
    role: str,
) -> None:
    ensure_can_read_audit_logs(role)


@pytest.mark.parametrize(
    "role",
    [
        "STAFF",
        "staff",
        "",
        " ",
        "MEMBER",
        "SYSTEM",
    ],
)
def test_other_roles_cannot_read_audit_logs(
    role: str,
) -> None:
    with pytest.raises(
        AuditLogAccessDeniedError,
    ):
        ensure_can_read_audit_logs(role)


def test_policy_rejects_non_string_role() -> None:
    with pytest.raises(
        AuditLogAccessDeniedError,
    ):
        ensure_can_read_audit_logs(
            object(),  # type: ignore[arg-type]
        )
