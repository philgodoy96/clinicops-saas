from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.identity.email import canonicalize_email
from clinicops.identity.exceptions import EmailAlreadyRegisteredError
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import (
    Argon2PasswordHasher,
    PasswordHasher,
    validate_password,
)
from clinicops.identity.repository import UserRepository


@dataclass(frozen=True, slots=True)
class CreateUserCommand:
    """Input required to create a password-backed global identity."""

    email: str
    password: str


@dataclass(frozen=True, slots=True)
class CreatedUser:
    """Public application result of global identity creation."""

    id: UUID
    email: str
    status: UserStatus
    created_at: datetime


class CreateUserService:
    """Create a global user and password credential in one transaction."""

    def __init__(
        self,
        repository: UserRepository | None = None,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self._repository = repository if repository is not None else UserRepository()
        self._password_hasher = (
            password_hasher if password_hasher is not None else Argon2PasswordHasher()
        )

    def execute(
        self,
        session: Session,
        command: CreateUserCommand,
    ) -> CreatedUser:
        """Create a global identity without committing the transaction."""

        canonical_email = canonicalize_email(command.email)
        validate_password(command.password)

        existing_user = self._repository.get_by_email(
            session,
            canonical_email,
        )
        if existing_user is not None:
            raise EmailAlreadyRegisteredError()

        password_hash = self._password_hasher.hash(command.password)
        user = User(
            email=canonical_email,
            password_credential=PasswordCredential(
                password_hash=password_hash,
            ),
        )
        self._repository.add_and_flush(session, user)

        return CreatedUser(
            id=user.id,
            email=user.email,
            status=user.status,
            created_at=user.created_at,
        )
