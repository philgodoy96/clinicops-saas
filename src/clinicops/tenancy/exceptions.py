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


class TenantNotFoundError(TenancyError):
    """Raised when a required tenant does not exist."""

    code = "tenant_not_found"
    public_message = "The tenant was not found."


class TenantDisabledError(TenancyError):
    """Raised when an operation requires an active tenant."""

    code = "tenant_disabled"
    public_message = "The tenant is disabled."


class MembershipNotFoundError(TenancyError):
    """Raised when a required tenant membership does not exist."""

    code = "membership_not_found"
    public_message = "The membership was not found."


class MembershipDisabledError(TenancyError):
    """Raised when an operation requires an active membership."""

    code = "membership_disabled"
    public_message = "The membership is disabled."


class TenantOwnershipConflictError(TenancyError):
    """Raised when ownership changed from the caller's expected state."""

    code = "tenant_ownership_conflict"
    public_message = "The tenant ownership state has changed."


class InvalidOwnershipTransferError(TenancyError):
    """Raised when an ownership transfer is not a valid state change."""

    code = "invalid_ownership_transfer"
    public_message = "The ownership transfer is invalid."


class MembershipActorNotAuthorizedError(TenancyError):
    """Raised when the actor cannot administer tenant memberships."""

    code = "membership_actor_not_authorized"
    public_message = "The actor cannot manage tenant memberships."


class MembershipRoleNotAllowedError(TenancyError):
    """Raised when a generic membership update requests a forbidden role."""

    code = "membership_role_not_allowed"
    public_message = "The membership role is not allowed."


class MembershipOwnerProtectedError(TenancyError):
    """Raised when a generic workflow targets the active owner."""

    code = "membership_owner_protected"
    public_message = "The tenant owner membership is protected."


class MembershipSelfManagementNotAllowedError(TenancyError):
    """Raised when an actor targets their own membership."""

    code = "membership_self_management_not_allowed"
    public_message = "The actor cannot manage their own membership."


class MembershipAlreadyDisabledError(TenancyError):
    """Raised when a disabled membership is disabled again."""

    code = "membership_already_disabled"
    public_message = "The membership is already disabled."


class MembershipAlreadyActiveError(TenancyError):
    """Raised when an active membership is enabled again."""

    code = "membership_already_active"
    public_message = "The membership is already active."
