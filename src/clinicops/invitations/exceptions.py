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


class InvitationTokenInvalidError(InvitationError):
    """Raised when a supplied invitation token cannot be resolved."""

    code = "invitation_token_invalid"
    public_message = "The invitation token is invalid."


class InvitationExpiredError(InvitationError):
    """Raised when an invitation is no longer within its validity window."""

    code = "invitation_expired"
    public_message = "The invitation has expired."


class InvitationRevokedError(InvitationError):
    """Raised when a revoked invitation is used."""

    code = "invitation_revoked"
    public_message = "The invitation has been revoked."


class InvitationAlreadyAcceptedError(InvitationError):
    """Raised when an accepted invitation is replayed."""

    code = "invitation_already_accepted"
    public_message = "The invitation has already been accepted."


class InvitationPasswordRequiredError(InvitationError):
    """Raised when a new global user requires a password."""

    code = "invitation_password_required"
    public_message = "A password is required to accept the invitation."
