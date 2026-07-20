from clinicops.core.exceptions import ApplicationError


class AuthenticationError(ApplicationError):
    """Base class for expected authentication failures."""

    code = "authentication_error"
    public_message = "Authentication could not be completed."


class AuthenticationConfigurationError(AuthenticationError):
    """Raised when authentication token configuration is unsafe."""

    code = "authentication_configuration_error"
    public_message = "Authentication token configuration is invalid."


class InvalidCredentialsError(AuthenticationError):
    """Raised when supplied credentials cannot authenticate a user."""

    code = "invalid_credentials"
    public_message = "The email or password is invalid."


class AuthenticationSessionNotFoundError(AuthenticationError):
    """Raised when an authentication session is unavailable."""

    code = "authentication_session_not_found"
    public_message = "The authentication session was not found."


class AuthenticationSessionInactiveError(AuthenticationError):
    """Raised when an authentication session cannot be used."""

    code = "authentication_session_inactive"
    public_message = "The authentication session is not active."


class AuthenticationSessionExpiredError(AuthenticationError):
    """Raised when an authentication session has expired."""

    code = "authentication_session_expired"
    public_message = "The authentication session has expired."


class AccessTokenInvalidError(AuthenticationError):
    """Raised when an access token cannot be trusted."""

    code = "access_token_invalid"
    public_message = "The access token is invalid."


class AccessTokenExpiredError(AuthenticationError):
    """Raised when an access token has reached its expiration."""

    code = "access_token_expired"
    public_message = "The access token has expired."


class RefreshTokenInvalidError(AuthenticationError):
    """Raised when a refresh token cannot be trusted."""

    code = "refresh_token_invalid"
    public_message = "The refresh token is invalid."


class RefreshTokenExpiredError(AuthenticationError):
    """Raised when a refresh token or its session has expired."""

    code = "refresh_token_expired"
    public_message = "The refresh token has expired."
