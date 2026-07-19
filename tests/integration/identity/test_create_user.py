from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.exceptions import EmailAlreadyRegisteredError
from clinicops.identity.models import PasswordCredential, User, UserStatus
from clinicops.identity.passwords import Argon2PasswordHasher
from clinicops.identity.repository import UserRepository
from clinicops.identity.service import (
    CreateUserCommand,
    CreateUserService,
)


class RecordingPasswordHasher:
    """Record hashing calls without performing expensive password hashing."""

    def __init__(self) -> None:
        self.hash_calls = 0

    def hash(self, password: str) -> str:
        self.hash_calls += 1
        return f"$argon2id$recording-hash-{self.hash_calls}"

    def verify(self, password: str, encoded_hash: str) -> bool:
        return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        return False


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide a database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_create_user_persists_canonical_identity_and_credential(
    db_session: Session,
) -> None:
    unique_part = uuid4()
    plaintext_password = "Correct horse battery staple"
    hasher = Argon2PasswordHasher()
    service = CreateUserService(password_hasher=hasher)

    created_user = service.execute(
        db_session,
        CreateUserCommand(
            email=f"  USER-{unique_part}@EXAMPLE.COM  ",
            password=plaintext_password,
        ),
    )

    stored_user = db_session.scalar(select(User).where(User.id == created_user.id))

    assert stored_user is not None
    assert created_user.email == f"user-{unique_part}@example.com"
    assert created_user.status is UserStatus.ACTIVE
    assert created_user.created_at.tzinfo is not None
    assert not hasattr(created_user, "password")
    assert not hasattr(created_user, "password_hash")

    stored_credential = stored_user.password_credential

    assert stored_credential is not None
    assert stored_credential.password_hash != plaintext_password
    assert plaintext_password not in stored_credential.password_hash
    assert (
        hasher.verify(
            plaintext_password,
            stored_credential.password_hash,
        )
        is True
    )


def test_create_user_rejects_existing_canonical_email_before_hashing(
    db_session: Session,
) -> None:
    email = f"duplicate-{uuid4()}@example.com"
    hasher = RecordingPasswordHasher()
    service = CreateUserService(password_hasher=hasher)

    service.execute(
        db_session,
        CreateUserCommand(
            email=email,
            password="Correct horse battery staple",
        ),
    )

    with pytest.raises(EmailAlreadyRegisteredError):
        service.execute(
            db_session,
            CreateUserCommand(
                email=f"  {email.upper()}  ",
                password="Another correct horse battery staple",
            ),
        )

    assert hasher.hash_calls == 1


def test_create_user_does_not_commit_the_transaction() -> None:
    email = f"rollback-{uuid4()}@example.com"
    service = CreateUserService()

    with Session(get_engine()) as session:
        service.execute(
            session,
            CreateUserCommand(
                email=email,
                password="Correct horse battery staple",
            ),
        )
        session.rollback()

    with Session(get_engine()) as verification_session:
        stored_user = verification_session.scalar(select(User).where(User.email == email))

    assert stored_user is None


def test_user_repository_reads_by_canonical_email(
    db_session: Session,
) -> None:
    email = f"repository-{uuid4()}@example.com"
    user = User(
        email=email,
        password_credential=PasswordCredential(
            password_hash="$argon2id$repository-test-hash",
        ),
    )
    repository = UserRepository()

    repository.add_and_flush(db_session, user)

    stored_user = repository.get_by_email(db_session, email)

    assert stored_user is user
