from clinicops.core.exceptions import ApplicationError


class AuthenticationError(ApplicationError):
    """Base class for expected authentication failures."""

    code = "authentication_error"
    public_message = "Authentication could not be completed."


class AuthenticationConfigurationError(AuthenticationError):
    """Raised when authentication token configuration is unsafe."""

    code = "authentication_configuration_error"
    public_message = "Authentication token configuration is invalid."


class AccessTokenInvalidError(AuthenticationError):
    """Raised when an access token cannot be trusted."""

    code = "access_token_invalid"
    public_message = "The access token is invalid."


class AccessTokenExpiredError(AuthenticationError):
    """Raised when an access token has reached its expiration."""

    code = "access_token_expired"
    public_message = "The access token has expired."


class RefreshTokenInvalidError(AuthenticationError):
    """Raised when a refresh token is malformed."""

    code = "refresh_token_invalid"
    public_message = "The refresh token is invalid."
