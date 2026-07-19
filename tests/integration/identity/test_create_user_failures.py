from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from clinicops.db.session import get_engine
from clinicops.identity.exceptions import EmailAlreadyRegisteredError
from clinicops.identity.models import PasswordCredential, User
from clinicops.identity.repository import UserRepository
from clinicops.identity.service import (
    CreateUserCommand,
    CreateUserService,
)


class StaticPasswordHasher:
    """Provide deterministic password hashes for concurrency tests."""

    def hash(self, password: str) -> str:
        return "$argon2id$concurrency-test-hash"

    def verify(self, password: str, encoded_hash: str) -> bool:
        return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        return False


class OverlongPasswordHasher:
    """Produce a hash that violates the credential column length."""

    def hash(self, password: str) -> str:
        return "x" * 513

    def verify(self, password: str, encoded_hash: str) -> bool:
        return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        return False


class CoordinatedUserRepository(UserRepository):
    """Force concurrent requests to complete their pre-check together."""

    def __init__(self, precheck_barrier: Barrier) -> None:
        self._precheck_barrier = precheck_barrier

    def get_by_email(
        self,
        session: Session,
        canonical_email: str,
    ) -> User | None:
        user = super().get_by_email(session, canonical_email)
        self._precheck_barrier.wait(timeout=5)
        return user


def create_user_in_independent_transaction(
    email: str,
    repository: UserRepository,
) -> str:
    """Create one user and report whether creation or conflict won."""

    service = CreateUserService(
        repository=repository,
        password_hasher=StaticPasswordHasher(),
    )

    with Session(get_engine()) as session:
        try:
            service.execute(
                session,
                CreateUserCommand(
                    email=email,
                    password="Correct horse battery staple",
                ),
            )
            session.commit()
        except EmailAlreadyRegisteredError:
            session.rollback()
            return "duplicate"

    return "created"


def test_concurrent_creation_allows_one_global_user() -> None:
    canonical_email = f"concurrent-{uuid4()}@example.com"
    repository = CoordinatedUserRepository(Barrier(2))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                create_user_in_independent_transaction,
                f"  {canonical_email.upper()}  ",
                repository,
            )
            second_future = executor.submit(
                create_user_in_independent_transaction,
                canonical_email,
                repository,
            )

            results = [
                first_future.result(timeout=10),
                second_future.result(timeout=10),
            ]

        assert sorted(results) == ["created", "duplicate"]

        with Session(get_engine()) as verification_session:
            stored_users = list(
                verification_session.scalars(select(User).where(User.email == canonical_email))
            )

            assert len(stored_users) == 1
            assert stored_users[0].password_credential is not None
    finally:
        with Session(get_engine()) as cleanup_session:
            cleanup_session.execute(delete(User).where(User.email == canonical_email))
            cleanup_session.commit()


def test_credential_failure_rolls_back_user_creation() -> None:
    email = f"atomic-{uuid4()}@example.com"
    service = CreateUserService(
        password_hasher=OverlongPasswordHasher(),
    )

    with Session(get_engine()) as session:
        with pytest.raises(DataError):
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


def test_repository_preserves_unrelated_integrity_errors() -> None:
    shared_user_id = uuid4()
    existing_email = f"existing-{uuid4()}@example.com"
    conflicting_email = f"conflicting-{uuid4()}@example.com"

    try:
        with Session(get_engine()) as setup_session:
            setup_session.add(
                User(
                    id=shared_user_id,
                    email=existing_email,
                    password_credential=PasswordCredential(
                        password_hash="$argon2id$existing-test-hash",
                    ),
                )
            )
            setup_session.commit()

        repository = UserRepository()

        with Session(get_engine()) as conflicting_session:
            conflicting_user = User(
                id=shared_user_id,
                email=conflicting_email,
                password_credential=PasswordCredential(
                    password_hash="$argon2id$conflicting-test-hash",
                ),
            )

            with pytest.raises(IntegrityError):
                repository.add_and_flush(
                    conflicting_session,
                    conflicting_user,
                )

            conflicting_session.rollback()
    finally:
        with Session(get_engine()) as cleanup_session:
            cleanup_session.execute(delete(User).where(User.id == shared_user_id))
            cleanup_session.commit()
