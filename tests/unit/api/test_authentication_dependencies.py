from types import SimpleNamespace
from typing import cast

from fastapi import Request
from pydantic import SecretStr

from clinicops.api.dependencies import get_application_settings
from clinicops.api.v1.authentication.dependencies import (
    get_access_token_codec,
    get_authenticate_user_service,
    get_authentication_token_config,
    get_password_hasher,
    get_refresh_authentication_service,
    get_resolve_authenticated_principal_service,
    get_revoke_authentication_session_service,
)
from clinicops.authentication.access_tokens import AccessTokenCodec
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
from clinicops.core.config import Environment, Settings
from clinicops.identity.passwords import Argon2PasswordHasher
from tests.conftest import IsolatedSettings

SIGNING_KEY = "test-signing-key-with-at-least-32-bytes"


def build_settings() -> Settings:
    """Build isolated test settings."""

    return IsolatedSettings(
        environment=Environment.TEST,
        auth_issuer="clinicops-test",
        auth_audience="clinicops-test-api",
        auth_signing_key=SecretStr(SIGNING_KEY),
    )


def test_application_settings_come_from_app_state() -> None:
    settings = build_settings()
    request = cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))),
    )

    resolved_settings = get_application_settings(request)

    assert resolved_settings is settings


def test_token_config_is_built_from_application_settings() -> None:
    settings = build_settings()

    config = get_authentication_token_config(settings)

    assert config.issuer == settings.auth_issuer
    assert config.audience == settings.auth_audience
    assert config.signing_key == SIGNING_KEY
    assert SIGNING_KEY not in repr(settings)
    assert SIGNING_KEY not in repr(config)


def test_access_token_codec_is_built_from_token_config() -> None:
    config = get_authentication_token_config(build_settings())

    codec = get_access_token_codec(config)

    assert isinstance(codec, AccessTokenCodec)


def test_login_dependencies_build_authentication_service() -> None:
    config = get_authentication_token_config(build_settings())
    codec = get_access_token_codec(config)
    password_hasher = get_password_hasher()

    service = get_authenticate_user_service(
        access_token_codec=codec,
        password_hasher=password_hasher,
    )

    assert isinstance(password_hasher, Argon2PasswordHasher)
    assert isinstance(service, AuthenticateUserService)


def test_refresh_dependency_builds_rotation_service() -> None:
    config = get_authentication_token_config(build_settings())
    codec = get_access_token_codec(config)

    service = get_refresh_authentication_service(codec)

    assert isinstance(service, RefreshAuthenticationService)


def test_principal_dependency_builds_resolution_service() -> None:
    config = get_authentication_token_config(build_settings())
    codec = get_access_token_codec(config)

    service = get_resolve_authenticated_principal_service(codec)

    assert isinstance(
        service,
        ResolveAuthenticatedPrincipalService,
    )


def test_revoke_dependency_builds_session_service() -> None:
    service = get_revoke_authentication_session_service()

    assert isinstance(
        service,
        RevokeAuthenticationSessionService,
    )
