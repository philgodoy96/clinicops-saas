from clinicops.core.exceptions import ApplicationError


class InvitationError(ApplicationError):
    """Base class for expected invitation lifecycle failures."""

    code = "invitation_error"
    public_message = "The invitation operation could not be completed."


class InvitationAlreadyPendingError(InvitationError):
    """Raised when an unexpired pending invitation already exists."""

    code = "invitation_already_pending"
    public_message = "An invitation is already pending."


class InvitationRoleNotAllowedError(InvitationError):
    """Raised when an invitation requests a forbidden tenant role."""

    code = "invitation_role_not_allowed"
    public_message = "The invitation role is not allowed."


class InvitationIssuerNotAuthorizedError(InvitationError):
    """Raised when the actor cannot issue invitations for the tenant."""

    code = "invitation_issuer_not_authorized"
    public_message = "The actor cannot issue invitations."


class InvitationMembershipAlreadyExistsError(InvitationError):
    """Raised when the invited user already has a tenant membership."""

    code = "invitation_membership_already_exists"
    public_message = "The invited user already has a membership."
