from enum import StrEnum

from clinicops.core.exceptions import ApplicationError


class TenancyError(ApplicationError):
    """Base class for expected tenant and membership failures."""

    code = "tenancy_error"
    public_message = "The tenant operation could not be completed."


class TenantNameViolation(StrEnum):
    """Machine-readable reasons for tenant name rejection."""

    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"


class InvalidTenantNameError(TenancyError):
    """Raised when a tenant display name violates the name policy."""

    code = "invalid_tenant_name"
    public_message = "The tenant name is invalid."

    def __init__(self, violation: TenantNameViolation) -> None:
        self.violation = violation
        super().__init__(f"Tenant name violation: {violation.value}")
