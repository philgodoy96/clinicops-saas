import pytest
from argon2 import PasswordHasher as Argon2LibraryPasswordHasher
from argon2.exceptions import InvalidHashError

from clinicops.identity.exceptions import (
    PasswordPolicyViolation,
    PasswordPolicyViolationError,
)
from clinicops.identity.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    Argon2PasswordHasher,
    validate_password,
)


def test_password_policy_accepts_unicode_whitespace_and_passphrases() -> None:
    validate_password("Correct horse 🐎 battery staple")


def test_password_policy_rejects_short_password() -> None:
    with pytest.raises(PasswordPolicyViolationError) as exception_info:
        validate_password("short")

    assert exception_info.value.violation is PasswordPolicyViolation.TOO_SHORT


def test_password_policy_rejects_long_password() -> None:
    password = "a" * (MAX_PASSWORD_LENGTH + 1)

    with pytest.raises(PasswordPolicyViolationError) as exception_info:
        validate_password(password)

    assert exception_info.value.violation is PasswordPolicyViolation.TOO_LONG


def test_password_policy_accepts_boundary_lengths() -> None:
    validate_password("a" * MIN_PASSWORD_LENGTH)
    validate_password("a" * MAX_PASSWORD_LENGTH)


def test_argon2_hash_does_not_store_plaintext_and_uses_random_salt() -> None:
    password = "Correct horse battery staple"
    hasher = Argon2PasswordHasher()

    first_hash = hasher.hash(password)
    second_hash = hasher.hash(password)

    assert first_hash.startswith("$argon2id$")
    assert second_hash.startswith("$argon2id$")
    assert password not in first_hash
    assert first_hash != second_hash


def test_argon2_verification_accepts_match_and_rejects_mismatch() -> None:
    password = "Correct horse battery staple"
    hasher = Argon2PasswordHasher()
    encoded_hash = hasher.hash(password)

    assert hasher.verify(password, encoded_hash) is True
    assert hasher.verify("different password", encoded_hash) is False


def test_current_argon2_hash_does_not_need_rehash() -> None:
    hasher = Argon2PasswordHasher()
    encoded_hash = hasher.hash("Correct horse battery staple")

    assert hasher.needs_rehash(encoded_hash) is False


def test_hash_with_different_parameters_needs_rehash() -> None:
    password = "Correct horse battery staple"
    legacy_library_hasher = Argon2LibraryPasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
    )
    current_library_hasher = Argon2LibraryPasswordHasher(
        time_cost=2,
        memory_cost=8192,
        parallelism=1,
    )
    encoded_hash = legacy_library_hasher.hash(password)
    hasher = Argon2PasswordHasher(current_library_hasher)

    assert hasher.needs_rehash(encoded_hash) is True


def test_invalid_encoded_hash_is_not_treated_as_password_mismatch() -> None:
    hasher = Argon2PasswordHasher()

    with pytest.raises(InvalidHashError):
        hasher.verify(
            "Correct horse battery staple",
            "not-an-argon2-hash",
        )
