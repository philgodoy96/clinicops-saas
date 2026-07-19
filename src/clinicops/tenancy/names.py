from clinicops.tenancy.exceptions import (
    InvalidTenantNameError,
    TenantNameViolation,
)

MIN_TENANT_NAME_LENGTH = 2
MAX_TENANT_NAME_LENGTH = 120


def normalize_tenant_name(raw_name: str) -> str:
    """Validate and return a tenant display name."""

    normalized_name = raw_name.strip()

    if len(normalized_name) < MIN_TENANT_NAME_LENGTH:
        raise InvalidTenantNameError(TenantNameViolation.TOO_SHORT)

    if len(normalized_name) > MAX_TENANT_NAME_LENGTH:
        raise InvalidTenantNameError(TenantNameViolation.TOO_LONG)

    return normalized_name
