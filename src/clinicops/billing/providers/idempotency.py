import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Final
from uuid import UUID

from clinicops.billing.enums import ProviderOperationType
from clinicops.billing.providers.exceptions import (
    InvalidProviderOperationKeyError,
)

PROVIDER_OPERATION_KEY_PREFIX: Final = "clinicops:"
MAX_PROVIDER_OPERATION_KEY_LENGTH: Final = 255

type ProviderFingerprintValue = str | int | bool | None | UUID | datetime | StrEnum
type NormalizedFingerprintValue = str | int | bool | None


def build_provider_operation_key(
    provider_operation_id: UUID,
) -> str:
    """Build the stable key sent to a payment provider."""

    provider_operation_key = f"{PROVIDER_OPERATION_KEY_PREFIX}{provider_operation_id}"

    if len(provider_operation_key) > MAX_PROVIDER_OPERATION_KEY_LENGTH:
        raise InvalidProviderOperationKeyError()

    return provider_operation_key


def validate_provider_operation_key(
    raw_key: str,
) -> str:
    """Validate and normalize an internal provider operation key."""

    normalized_key = raw_key.strip()

    if (
        not normalized_key
        or len(normalized_key) > MAX_PROVIDER_OPERATION_KEY_LENGTH
        or not normalized_key.startswith(PROVIDER_OPERATION_KEY_PREFIX)
    ):
        raise InvalidProviderOperationKeyError()

    identifier = normalized_key.removeprefix(PROVIDER_OPERATION_KEY_PREFIX)

    try:
        provider_operation_id = UUID(identifier)
    except ValueError as error:
        raise InvalidProviderOperationKeyError() from error

    if str(provider_operation_id) != identifier:
        raise InvalidProviderOperationKeyError()

    return build_provider_operation_key(provider_operation_id)


def fingerprint_provider_request(
    *,
    operation_type: ProviderOperationType,
    fields: Mapping[str, ProviderFingerprintValue],
) -> str:
    """Return a deterministic digest for a provider request."""

    normalized_fields = {
        field_name: _normalize_fingerprint_value(value) for field_name, value in fields.items()
    }

    payload = {
        "fields": normalized_fields,
        "operation_type": operation_type.value,
    }

    canonical_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    return sha256(canonical_payload.encode("utf-8")).hexdigest()


def _normalize_fingerprint_value(
    value: ProviderFingerprintValue,
) -> NormalizedFingerprintValue:
    if isinstance(value, StrEnum):
        return value.value

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Provider request datetimes must be timezone-aware.")

        return value.astimezone(UTC).isoformat()

    if isinstance(value, UUID):
        return str(value)

    if value is None or isinstance(value, str | int | bool):
        return value

    raise TypeError("Unsupported provider fingerprint value type.")
