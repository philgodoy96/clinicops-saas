from typing import Protocol

from argon2 import PasswordHasher as Argon2LibraryPasswordHasher
from argon2.exceptions import VerifyMismatchError

from clinicops.identity.exceptions import (
    PasswordPolicyViolation,
    PasswordPolicyViolationError,
)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def validate_password(password: str) -> None:
    """Validate a password intended for a new credential."""

    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyViolationError(
            PasswordPolicyViolation.TOO_SHORT,
        )

    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyViolationError(
            PasswordPolicyViolation.TOO_LONG,
        )


class PasswordHasher(Protocol):
    """Password hashing behavior required by identity services."""

    def hash(self, password: str) -> str:
        """Validate and hash a plaintext password."""
        ...

    def verify(self, password: str, encoded_hash: str) -> bool:
        """Return whether a plaintext password matches an encoded hash."""
        ...

    def needs_rehash(self, encoded_hash: str) -> bool:
        """Return whether an encoded hash uses outdated parameters."""
        ...


class Argon2PasswordHasher:
    """Argon2id implementation of the password hashing boundary."""

    def __init__(
        self,
        hasher: Argon2LibraryPasswordHasher | None = None,
    ) -> None:
        self._hasher = hasher or Argon2LibraryPasswordHasher()

    def hash(self, password: str) -> str:
        """Validate and hash a plaintext password with a random salt."""

        validate_password(password)
        return self._hasher.hash(password)

    def verify(self, password: str, encoded_hash: str) -> bool:
        """Return false only when the supplied password does not match."""

        try:
            return self._hasher.verify(encoded_hash, password)
        except VerifyMismatchError:
            return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        """Return whether the hash differs from the current parameters."""

        return self._hasher.check_needs_rehash(encoded_hash)
