from __future__ import annotations

import sys
from dataclasses import dataclass
from uuid import UUID

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.email import canonicalize_email
from clinicops.identity.exceptions import EmailAlreadyRegisteredError
from clinicops.identity.repository import UserRepository
from clinicops.identity.service import CreateUserCommand, CreateUserService


class BootstrapUserSettings(BaseSettings):
    """CLI-only configuration for the initial local user."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CLINICOPS_BOOTSTRAP_USER_",
        case_sensitive=False,
        extra="ignore",
    )

    email: str
    password: SecretStr


@dataclass(frozen=True)
class BootstrapUserResult:
    """Safe operational result returned by the bootstrap workflow."""

    user_id: UUID
    email: str
    created: bool


def bootstrap_user(
    session: Session,
    settings: BootstrapUserSettings,
    *,
    repository: UserRepository | None = None,
    service: CreateUserService | None = None,
) -> BootstrapUserResult:
    """Create the initial local user without replacing existing credentials."""

    user_repository = repository or UserRepository()
    create_user_service = service or CreateUserService(
        repository=user_repository,
    )

    canonical_email = canonicalize_email(settings.email)

    existing_user = user_repository.get_by_email(
        session,
        canonical_email,
    )

    if existing_user is not None:
        return BootstrapUserResult(
            user_id=existing_user.id,
            email=existing_user.email,
            created=False,
        )

    try:
        created_user = create_user_service.execute(
            session,
            CreateUserCommand(
                email=settings.email,
                password=settings.password.get_secret_value(),
            ),
        )
        session.commit()
    except EmailAlreadyRegisteredError:
        session.rollback()

        concurrent_user = user_repository.get_by_email(
            session,
            canonical_email,
        )

        if concurrent_user is None:
            raise

        return BootstrapUserResult(
            user_id=concurrent_user.id,
            email=concurrent_user.email,
            created=False,
        )
    except Exception:
        session.rollback()
        raise

    return BootstrapUserResult(
        user_id=created_user.id,
        email=created_user.email,
        created=True,
    )


def _print_configuration_errors(error: ValidationError) -> None:
    sys.stderr.write(
        "Local user bootstrap configuration is invalid.\n",
    )

    for issue in error.errors():
        location = ".".join(str(part) for part in issue["loc"])
        message = issue["msg"]
        sys.stderr.write(f"- {location}: {message}\n")


def main() -> int:
    """Execute the local user bootstrap command."""

    try:
        settings = BootstrapUserSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        _print_configuration_errors(error)
        return 2

    try:
        with Session(get_engine()) as session:
            result = bootstrap_user(session, settings)
    except Exception as error:
        sys.stderr.write(
            f"Local user bootstrap failed: {type(error).__name__}: {error}\n",
        )
        return 1

    if result.created:
        sys.stdout.write(
            "Local bootstrap user created successfully. "
            f"user_id={result.user_id} email={result.email}\n",
        )
    else:
        sys.stdout.write(
            "Local bootstrap user already exists; "
            "no database changes were made. "
            f"user_id={result.user_id} email={result.email}\n",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
