from clinicops.audit.exceptions import AuditLogError


class AuditLogAccessDeniedError(AuditLogError):
    code = "audit_log_access_denied"

    def __init__(self) -> None:
        super().__init__("The current membership cannot read tenant audit logs.")


_ALLOWED_AUDIT_LOG_ROLES = frozenset(
    {
        "OWNER",
        "ADMIN",
    }
)


def ensure_can_read_audit_logs(
    role: str,
) -> None:
    if not isinstance(role, str):
        raise AuditLogAccessDeniedError()

    normalized_role = role.strip().upper()

    if normalized_role not in _ALLOWED_AUDIT_LOG_ROLES:
        raise AuditLogAccessDeniedError()


__all__ = [
    "AuditLogAccessDeniedError",
    "ensure_can_read_audit_logs",
]
