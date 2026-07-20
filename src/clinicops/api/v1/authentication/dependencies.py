from typing import Annotated

from fastapi import Depends

from clinicops.api.dependencies import ApplicationSettingsDependency
from clinicops.authentication.access_tokens import AccessTokenCodec
from clinicops.authentication.config import AuthenticationTokenConfig
from clinicops.authentication.services.authenticate_user import (
    AuthenticateUserService,
)
from clinicops.authentication.services.refresh_authentication import (
    RefreshAuthenticationService,
)
from clinicops.authentication.services.resolve_principal import (
    ResolveAuthenticatedPrincipalService,
)
from clinicops.authentication.services.revoke_session import (
    RevokeAuthenticationSessionService,
)
from clinicops.identity.passwords import (
    Argon2PasswordHasher,
    PasswordHasher,
)


def get_authentication_token_config(
    settings: ApplicationSettingsDependency,
) -> AuthenticationTokenConfig:
    """Build validated token configuration from application settings."""

    return AuthenticationTokenConfig(
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        signing_key=settings.auth_signing_key.get_secret_value(),
    )


AuthenticationTokenConfigDependency = Annotated[
    AuthenticationTokenConfig,
    Depends(get_authentication_token_config),
]


def get_access_token_codec(
    config: AuthenticationTokenConfigDependency,
) -> AccessTokenCodec:
    """Build the access-token codec for one dependency graph."""

    return AccessTokenCodec(config)


AccessTokenCodecDependency = Annotated[
    AccessTokenCodec,
    Depends(get_access_token_codec),
]


def get_password_hasher() -> PasswordHasher:
    """Build the password hasher used by login authentication."""

    return Argon2PasswordHasher()


PasswordHasherDependency = Annotated[
    PasswordHasher,
    Depends(get_password_hasher),
]


def get_authenticate_user_service(
    access_token_codec: AccessTokenCodecDependency,
    password_hasher: PasswordHasherDependency,
) -> AuthenticateUserService:
    """Build the global login authentication service."""

    return AuthenticateUserService(
        access_token_codec=access_token_codec,
        password_hasher=password_hasher,
    )


AuthenticateUserServiceDependency = Annotated[
    AuthenticateUserService,
    Depends(get_authenticate_user_service),
]


def get_refresh_authentication_service(
    access_token_codec: AccessTokenCodecDependency,
) -> RefreshAuthenticationService:
    """Build the refresh-token rotation service."""

    return RefreshAuthenticationService(
        access_token_codec=access_token_codec,
    )


RefreshAuthenticationServiceDependency = Annotated[
    RefreshAuthenticationService,
    Depends(get_refresh_authentication_service),
]


def get_resolve_authenticated_principal_service(
    access_token_codec: AccessTokenCodecDependency,
) -> ResolveAuthenticatedPrincipalService:
    """Build the authenticated-principal resolution service."""

    return ResolveAuthenticatedPrincipalService(
        access_token_codec=access_token_codec,
    )


ResolveAuthenticatedPrincipalServiceDependency = Annotated[
    ResolveAuthenticatedPrincipalService,
    Depends(get_resolve_authenticated_principal_service),
]


def get_revoke_authentication_session_service() -> RevokeAuthenticationSessionService:
    """Build the current-session revocation service."""

    return RevokeAuthenticationSessionService()


RevokeAuthenticationSessionServiceDependency = Annotated[
    RevokeAuthenticationSessionService,
    Depends(get_revoke_authentication_session_service),
]
