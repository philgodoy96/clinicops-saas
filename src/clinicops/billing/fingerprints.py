import json
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256

from clinicops.billing.enums import ProviderOperationType

type FingerprintValue = str | int | bool | None


def fingerprint_billing_command(
    *,
    operation_type: ProviderOperationType,
    fields: Mapping[str, FingerprintValue],
) -> str:
    """Return a deterministic SHA-256 digest for a billing command."""

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
    value: FingerprintValue,
) -> FingerprintValue:
    if isinstance(value, StrEnum):
        return value.value

    return value
