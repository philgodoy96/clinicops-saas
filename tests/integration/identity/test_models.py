from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_user_and_password_credential_persist_as_one_identity(
    db_session: Session,
) -> None:
    email = f"user-{uuid4()}@example.com"
    password_hash = "$argon2id$test-persistence-hash"
    user = User(email=email)
    user.password_credential = PasswordCredential(
        password_hash=password_hash,
    )

    db_session.add(user)
    db_session.flush()

    user_id = user.id
    db_session.expire_all()

    stored_user = db_session.scalar(select(User).where(User.id == user_id))

    assert stored_user is not None
    assert stored_user.email == email
    assert stored_user.status is UserStatus.ACTIVE
    assert stored_user.disabled_at is None
    assert stored_user.created_at.tzinfo is not None
    assert stored_user.updated_at.tzinfo is not None

    stored_credential = stored_user.password_credential

    assert stored_credential is not None
    assert stored_credential.user_id == user_id
    assert stored_credential.password_hash == password_hash
    assert stored_credential.created_at.tzinfo is not None
    assert stored_credential.updated_at.tzinfo is not None


def test_users_enforce_global_email_uniqueness(
    db_session: Session,
) -> None:
    email = f"duplicate-{uuid4()}@example.com"

    db_session.add(User(email=email))
    db_session.flush()

    db_session.add(User(email=email))

    with pytest.raises(IntegrityError):
        db_session.flush()
