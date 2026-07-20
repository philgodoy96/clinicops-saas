from fastapi import APIRouter, Response, status

from clinicops.api.dependencies import DatabaseSessionDependency
from clinicops.api.v1.authentication.dependencies import (
    AuthenticatedPrincipalDependency,
    AuthenticateUserServiceDependency,
    RefreshAuthenticationServiceDependency,
    RevokeAuthenticationSessionServiceDependency,
)
from clinicops.api.v1.authentication.schemas import (
    CurrentPrincipalResponse,
    LoginRequest,
    RefreshRequest,
    TokenPairResponse,
)
from clinicops.authentication.exceptions import RefreshTokenInvalidError
from clinicops.authentication.services.authenticate_user import (
    AuthenticatedSession,
    AuthenticateUserCommand,
)
from clinicops.authentication.services.refresh_authentication import (
    CompromisedAuthenticationSession,
    RefreshAuthenticationCommand,
    RefreshedAuthentication,
)
from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
)
from clinicops.authentication.services.revoke_session import (
    RevokeAuthenticationSessionCommand,
)

router = APIRouter(tags=["authentication"])


def _token_pair_response(
    result: AuthenticatedSession | RefreshedAuthentication,
) -> TokenPairResponse:
    """Translate an application result into the HTTP token contract."""

    return TokenPairResponse(
        user_id=result.user_id,
        session_id=result.session_id,
        access_token=result.access_token,
        access_token_expires_at=result.access_token_expires_at,
        refresh_token=result.refresh_token,
        session_expires_at=result.session_expires_at,
    )


def _current_principal_response(
    principal: AuthenticatedPrincipal,
) -> CurrentPrincipalResponse:
    """Translate a trusted principal into its public HTTP contract."""

    return CurrentPrincipalResponse(
        user_id=principal.user_id,
        session_id=principal.session_id,
        authenticated_at=principal.authenticated_at,
        access_token_expires_at=principal.access_token_expires_at,
        session_expires_at=principal.session_expires_at,
    )


@router.post(
    "/login",
    response_model=TokenPairResponse,
    status_code=status.HTTP_200_OK,
    summary="Create an authentication session",
)
def login(
    payload: LoginRequest,
    session: DatabaseSessionDependency,
    service: AuthenticateUserServiceDependency,
) -> TokenPairResponse:
    """Authenticate a global user and commit issued credentials."""

    result = service.execute(
        session,
        AuthenticateUserCommand(
            email=payload.email,
            password=payload.password,
        ),
    )
    session.commit()

    return _token_pair_response(result)


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate authentication credentials",
)
def refresh_authentication(
    payload: RefreshRequest,
    session: DatabaseSessionDependency,
    service: RefreshAuthenticationServiceDependency,
) -> TokenPairResponse:
    """Rotate credentials or commit detected refresh-token reuse."""

    result = service.execute(
        session,
        RefreshAuthenticationCommand(
            refresh_token=payload.refresh_token,
        ),
    )
    session.commit()

    if isinstance(result, CompromisedAuthenticationSession):
        raise RefreshTokenInvalidError()

    return _token_pair_response(result)


@router.get(
    "/me",
    response_model=CurrentPrincipalResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the current authenticated principal",
)
def get_current_principal(
    principal: AuthenticatedPrincipalDependency,
) -> CurrentPrincipalResponse:
    """Return the trusted global principal for the current request."""

    return _current_principal_response(principal)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke the current authentication session",
)
def logout(
    principal: AuthenticatedPrincipalDependency,
    session: DatabaseSessionDependency,
    service: RevokeAuthenticationSessionServiceDependency,
) -> Response:
    """Revoke the authenticated session and commit the transition."""

    service.execute(
        session,
        RevokeAuthenticationSessionCommand(
            user_id=principal.user_id,
            session_id=principal.session_id,
        ),
    )
    session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
