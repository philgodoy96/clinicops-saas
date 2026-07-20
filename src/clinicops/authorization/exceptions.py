from clinicops.core.exceptions import ApplicationError


class TenantAuthorizationError(ApplicationError):
    """Base class for expected tenant authorization failures."""

    code = "tenant_authorization_error"
    public_message = "Tenant authorization could not be completed."


class TenantNotFoundError(TenantAuthorizationError):
    """Raised when the selected tenant does not exist."""

    code = "tenant_not_found"
    public_message = "The tenant was not found."


class TenantDisabledError(TenantAuthorizationError):
    """Raised when the selected tenant cannot authorize requests."""

    code = "tenant_disabled"
    public_message = "The tenant is disabled."


class TenantMembershipNotFoundError(TenantAuthorizationError):
    """Raised when the user has no membership in the selected tenant."""

    code = "tenant_membership_not_found"
    public_message = "The tenant membership was not found."


class TenantMembershipDisabledError(TenantAuthorizationError):
    """Raised when the user's tenant membership is disabled."""

    code = "tenant_membership_disabled"
    public_message = "The tenant membership is disabled."


class TenantPermissionDeniedError(TenantAuthorizationError):
    """Raised when the current tenant role lacks a permission."""

    code = "tenant_permission_denied"
    public_message = "The tenant operation is not permitted."
