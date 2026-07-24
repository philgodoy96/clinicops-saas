from clinicops.core.exceptions import ApplicationError


class ProfessionalError(ApplicationError):
    """Base class for expected professional-domain failures."""

    code = "professional_error"
    public_message = "The professional operation could not be completed."


class ProfessionalNotFoundError(ProfessionalError):
    """Raised when a professional is not visible in the authorized tenant."""

    code = "professional_not_found"
    public_message = "The professional was not found."


class ProfessionalAlreadyArchivedError(ProfessionalError):
    """Raised when an archive transition targets an archived professional."""

    code = "professional_already_archived"
    public_message = "The professional is already archived."


class ProfessionalNotArchivedError(ProfessionalError):
    """Raised when a restore transition targets an active professional."""

    code = "professional_not_archived"
    public_message = "The professional is not archived."


class ProfessionalVersionConflictError(ProfessionalError):
    """Raised when a mutation uses a stale professional version."""

    code = "professional_version_conflict"
    public_message = "The professional version is no longer current."


class ProfessionalExternalReferenceConflictError(ProfessionalError):
    """Raised when a tenant-local external reference is already in use."""

    code = "professional_external_reference_conflict"
    public_message = "A professional with the same external reference already exists."


class ProfessionalInvalidRegistrationError(ProfessionalError):
    """Raised when submitted professional registration data is invalid."""

    code = "professional_invalid_registration"
    public_message = "The professional registration data is invalid."


class ProfessionalInvalidUpdateError(ProfessionalError):
    """Raised when a professional patch cannot produce a valid mutation."""

    code = "professional_invalid_update"
    public_message = "The professional update is invalid."


class ProfessionalInvalidCursorError(ProfessionalError):
    """Raised when a professional pagination cursor is malformed."""

    code = "professional_invalid_cursor"
    public_message = "The professional cursor is invalid."


class ProfessionalAlreadyLinkedError(ProfessionalError):
    """Raised when a link targets a professional that already has a membership."""

    code = "professional_already_linked"
    public_message = "The professional is already linked to a membership."


class ProfessionalNotLinkedError(ProfessionalError):
    """Raised when an unlink targets a professional without a membership."""

    code = "professional_not_linked"
    public_message = "The professional is not linked to a membership."


class ProfessionalMembershipNotFoundError(ProfessionalError):
    """Raised when a membership is not visible within the authorized tenant."""

    code = "professional_membership_not_found"
    public_message = "The membership was not found."


class ProfessionalMembershipInactiveError(ProfessionalError):
    """Raised when an inactive membership is selected for linking."""

    code = "professional_membership_inactive"
    public_message = "The membership is not active."


class ProfessionalMembershipLinkConflictError(ProfessionalError):
    """Raised when a membership is already linked to another professional."""

    code = "professional_membership_link_conflict"
    public_message = "The membership is already linked to another professional."


__all__ = [
    "ProfessionalAlreadyArchivedError",
    "ProfessionalAlreadyLinkedError",
    "ProfessionalError",
    "ProfessionalExternalReferenceConflictError",
    "ProfessionalInvalidCursorError",
    "ProfessionalInvalidRegistrationError",
    "ProfessionalInvalidUpdateError",
    "ProfessionalMembershipInactiveError",
    "ProfessionalMembershipLinkConflictError",
    "ProfessionalMembershipNotFoundError",
    "ProfessionalNotArchivedError",
    "ProfessionalNotFoundError",
    "ProfessionalNotLinkedError",
    "ProfessionalVersionConflictError",
]
