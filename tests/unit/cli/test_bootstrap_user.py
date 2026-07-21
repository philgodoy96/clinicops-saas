from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

import clinicops.cli.bootstrap_user as bootstrap_module
from clinicops.cli.bootstrap_user import (
    BootstrapUserSettings,
    bootstrap_user,
)
from clinicops.identity.models import User, UserStatus
from clinicops.identity.repository import UserRepository
from clinicops.identity.service import (
    CreatedUser,
    CreateUserCommand,
    CreateUserService,
)


class RecordingSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


class RecordingUserRepository(UserRepository):
    def __init__(self, responses: list[User | None]) -> None:
        self._responses = iter(responses)
        self.requested_emails: list[str] = []

    def get_by_email(
        self,
        session: Session,
        email: str,
    ) -> User | None:
        del session

        self.requested_emails.append(email)
        return next(self._responses)


class RecordingCreateUserService(CreateUserService):
    def __init__(
        self,
        *,
        result: CreatedUser | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.commands: list[CreateUserCommand] = []

    def execute(
        self,
        session: Session,
        command: CreateUserCommand,
    ) -> CreatedUser:
        del session

        self.commands.append(command)

        if self.error is not None:
            raise self.error

        if self.result is None:
            raise AssertionError("A result was not configured.")

        return self.result


def build_settings(
    *,
    email: str = "Owner@Northstar.Example.com",
    password: str = "Owner-ClinicOps-2026!",
) -> BootstrapUserSettings:
    return BootstrapUserSettings.model_validate(
        {
            "email": email,
            "password": password,
        }
    )


def test_bootstrap_user_returns_existing_user_without_writes() -> None:
    existing_user = User(
        id=uuid4(),
        email="owner@northstar.example.com",
        status=UserStatus.ACTIVE,
    )
    repository = RecordingUserRepository([existing_user])
    service = RecordingCreateUserService()
    recording_session = RecordingSession()

    result = bootstrap_user(
        cast(Session, recording_session),
        build_settings(),
        repository=repository,
        service=service,
    )

    assert result.user_id == existing_user.id
    assert result.email == existing_user.email
    assert result.created is False

    assert repository.requested_emails == ["owner@northstar.example.com"]
    assert service.commands == []
    assert recording_session.commit_calls == 0
    assert recording_session.rollback_calls == 0


def test_bootstrap_user_commits_created_user() -> None:
    created_user = CreatedUser(
        id=uuid4(),
        email="owner@northstar.example.com",
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )
    repository = RecordingUserRepository([None])
    service = RecordingCreateUserService(result=created_user)
    recording_session = RecordingSession()

    result = bootstrap_user(
        cast(Session, recording_session),
        build_settings(),
        repository=repository,
        service=service,
    )

    assert result.user_id == created_user.id
    assert result.email == created_user.email
    assert result.created is True

    assert len(service.commands) == 1
    assert service.commands[0].email == "Owner@Northstar.Example.com"
    assert service.commands[0].password == "Owner-ClinicOps-2026!"

    assert recording_session.commit_calls == 1
    assert recording_session.rollback_calls == 0


def test_bootstrap_user_rolls_back_unexpected_failure() -> None:
    repository = RecordingUserRepository([None])
    service = RecordingCreateUserService(error=RuntimeError("database unavailable"))
    recording_session = RecordingSession()

    with pytest.raises(
        RuntimeError,
        match="database unavailable",
    ):
        bootstrap_user(
            cast(Session, recording_session),
            build_settings(),
            repository=repository,
            service=service,
        )

    assert recording_session.commit_calls == 0
    assert recording_session.rollback_calls == 1


def test_bootstrap_user_treats_concurrent_duplicate_as_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConcurrentDuplicateError(Exception):
        pass

    monkeypatch.setattr(
        bootstrap_module,
        "EmailAlreadyRegisteredError",
        ConcurrentDuplicateError,
    )

    concurrent_user = User(
        id=uuid4(),
        email="owner@northstar.example.com",
        status=UserStatus.ACTIVE,
    )
    repository = RecordingUserRepository(
        [
            None,
            concurrent_user,
        ]
    )
    service = RecordingCreateUserService(error=ConcurrentDuplicateError())
    recording_session = RecordingSession()

    result = bootstrap_module.bootstrap_user(
        cast(Session, recording_session),
        build_settings(),
        repository=repository,
        service=service,
    )

    assert result.user_id == concurrent_user.id
    assert result.email == concurrent_user.email
    assert result.created is False

    assert repository.requested_emails == [
        "owner@northstar.example.com",
        "owner@northstar.example.com",
    ]
    assert recording_session.commit_calls == 0
    assert recording_session.rollback_calls == 1


def test_bootstrap_user_reraises_unresolved_concurrent_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConcurrentDuplicateError(Exception):
        pass

    monkeypatch.setattr(
        bootstrap_module,
        "EmailAlreadyRegisteredError",
        ConcurrentDuplicateError,
    )

    repository = RecordingUserRepository(
        [
            None,
            None,
        ]
    )
    service = RecordingCreateUserService(error=ConcurrentDuplicateError())
    recording_session = RecordingSession()

    with pytest.raises(ConcurrentDuplicateError):
        bootstrap_module.bootstrap_user(
            cast(Session, recording_session),
            build_settings(),
            repository=repository,
            service=service,
        )

    assert recording_session.commit_calls == 0
    assert recording_session.rollback_calls == 1


def test_bootstrap_settings_reads_cli_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "CLINICOPS_BOOTSTRAP_USER_EMAIL",
        "owner@northstar.example.com",
    )
    monkeypatch.setenv(
        "CLINICOPS_BOOTSTRAP_USER_PASSWORD",
        "Owner-ClinicOps-2026!",
    )

    settings = BootstrapUserSettings()  # type: ignore[call-arg]

    assert settings.email == "owner@northstar.example.com"
    assert settings.password.get_secret_value() == "Owner-ClinicOps-2026!"


def test_bootstrap_settings_rejects_missing_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(
        "CLINICOPS_BOOTSTRAP_USER_EMAIL",
        raising=False,
    )
    monkeypatch.delenv(
        "CLINICOPS_BOOTSTRAP_USER_PASSWORD",
        raising=False,
    )

    with pytest.raises(ValidationError):
        BootstrapUserSettings()  # type: ignore[call-arg]
