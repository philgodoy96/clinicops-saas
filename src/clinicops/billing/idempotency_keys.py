from typing import Final

from clinicops.billing.exceptions import (
    IdempotencyKeyViolation,
    InvalidIdempotencyKeyError,
)

MAX_IDEMPOTENCY_KEY_LENGTH: Final = 255


def validate_idempotency_key(raw_key: str) -> str:
    """Validate and normalize an opaque client idempotency key."""

    normalized_key = raw_key.strip()

    if not normalized_key:
        raise InvalidIdempotencyKeyError(IdempotencyKeyViolation.EMPTY)

    if len(normalized_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise InvalidIdempotencyKeyError(IdempotencyKeyViolation.TOO_LONG)

    return normalized_key
