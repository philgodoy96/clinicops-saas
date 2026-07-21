import pytest

from clinicops.billing.exceptions import (
    IdempotencyKeyViolation,
    InvalidIdempotencyKeyError,
)
from clinicops.billing.idempotency_keys import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    validate_idempotency_key,
)


def test_idempotency_key_accepts_an_opaque_value() -> None:
    key = validate_idempotency_key("subscription-attempt-client-reference")

    assert key == "subscription-attempt-client-reference"


def test_idempotency_key_does_not_require_a_uuid() -> None:
    key = validate_idempotency_key("mobile-app:retry:01")

    assert key == "mobile-app:retry:01"


def test_idempotency_key_strips_external_whitespace() -> None:
    key = validate_idempotency_key("  checkout-attempt-01  ")

    assert key == "checkout-attempt-01"


def test_idempotency_key_preserves_case_and_internal_content() -> None:
    key = validate_idempotency_key("Client Request 01")

    assert key == "Client Request 01"


def test_idempotency_key_accepts_the_maximum_length() -> None:
    raw_key = "a" * MAX_IDEMPOTENCY_KEY_LENGTH

    assert validate_idempotency_key(raw_key) == raw_key


@pytest.mark.parametrize(
    "raw_key",
    [
        "",
        " ",
        "\t",
        "\n",
        " \t\n ",
    ],
)
def test_idempotency_key_rejects_empty_normalized_values(
    raw_key: str,
) -> None:
    with pytest.raises(InvalidIdempotencyKeyError) as exception_info:
        validate_idempotency_key(raw_key)

    assert exception_info.value.violation is IdempotencyKeyViolation.EMPTY


def test_idempotency_key_rejects_values_over_the_limit() -> None:
    raw_key = "a" * (MAX_IDEMPOTENCY_KEY_LENGTH + 1)

    with pytest.raises(InvalidIdempotencyKeyError) as exception_info:
        validate_idempotency_key(raw_key)

    assert exception_info.value.violation is IdempotencyKeyViolation.TOO_LONG


@pytest.mark.parametrize(
    ("violation", "expected_value"),
    [
        (
            IdempotencyKeyViolation.EMPTY,
            "empty",
        ),
        (
            IdempotencyKeyViolation.TOO_LONG,
            "too_long",
        ),
    ],
)
def test_idempotency_key_violations_have_stable_values(
    violation: IdempotencyKeyViolation,
    expected_value: str,
) -> None:
    assert violation.value == expected_value


def test_invalid_idempotency_key_error_has_a_stable_contract() -> None:
    error = InvalidIdempotencyKeyError(IdempotencyKeyViolation.EMPTY)

    assert error.code == "invalid_idempotency_key"
    assert error.public_message == "The billing idempotency key is invalid."
    assert error.violation is IdempotencyKeyViolation.EMPTY
