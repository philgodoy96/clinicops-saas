from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

EmailInput = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=320,
    ),
]


class AuthenticationApiModel(BaseModel):
    """Base configuration for authentication transport contracts."""

    model_config = ConfigDict(extra="forbid")


class LoginRequest(AuthenticationApiModel):
    """Credentials submitted to create an authentication session."""

    email: EmailInput
    password: str = Field(
        min_length=1,
        max_length=1024,
        repr=False,
    )


class RefreshRequest(AuthenticationApiModel):
    """Opaque refresh token submitted for credential rotation."""

    refresh_token: str = Field(
        min_length=1,
        max_length=2048,
        repr=False,
    )


class TokenPairResponse(AuthenticationApiModel):
    """Newly issued access and refresh credentials."""

    user_id: UUID
    session_id: UUID
    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    access_token_expires_at: datetime
    refresh_token: str = Field(repr=False)
    session_expires_at: datetime


class CurrentPrincipalResponse(AuthenticationApiModel):
    """Current trusted global authentication context."""

    user_id: UUID
    session_id: UUID
    authenticated_at: datetime
    access_token_expires_at: datetime
    session_expires_at: datetime
