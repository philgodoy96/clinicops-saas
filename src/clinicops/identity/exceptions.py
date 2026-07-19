from enum import StrEnum

from clinicops.core.exceptions import ApplicationError


class IdentityError(ApplicationError):
    """Base class for expected global identity failures."""

    code = "identity_error"
    public_message = "The identity operation could not be completed."


class InvalidEmailError(IdentityError):
    """Raised when an email address cannot become a canonical identity."""

    code = "invalid_email"
    public_message = "The email address is invalid."


class PasswordPolicyViolation(StrEnum):
    """Machine-readable reasons for password policy rejection."""

    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"


class PasswordPolicyViolationError(IdentityError):
    """Raised when a password does not satisfy the creation policy."""

    code = "password_policy_violation"
    public_message = "The password does not satisfy the security policy."

    def __init__(self, violation: PasswordPolicyViolation) -> None:
        self.violation = violation
        super().__init__(f"Password policy violation: {violation.value}")


class EmailAlreadyRegisteredError(IdentityError):
    """Raised when a canonical email already belongs to a global user."""

    code = "email_already_registered"
    public_message = "The email address is already registered."
