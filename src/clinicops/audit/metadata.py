import json
import math

from clinicops.audit.contracts import (
    JSONObject,
    JSONValue,
)
from clinicops.audit.exceptions import (
    AuditLogInvalidMetadataError,
)

MAX_METADATA_DEPTH = 6
MAX_OBJECT_KEYS = 50
MAX_ARRAY_ITEMS = 100
MAX_OBJECT_KEY_LENGTH = 100
MAX_STRING_VALUE_LENGTH = 2_000
MAX_SERIALIZED_METADATA_BYTES = 16 * 1024


def normalize_audit_metadata(
    value: object,
) -> JSONObject:
    if type(value) is not dict:
        raise AuditLogInvalidMetadataError("Audit metadata must be a JSON object.")

    normalized = _normalize_object(
        value,
        depth=1,
    )

    serialized = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    if len(serialized) > MAX_SERIALIZED_METADATA_BYTES:
        raise AuditLogInvalidMetadataError(
            f"Audit metadata must not exceed {MAX_SERIALIZED_METADATA_BYTES} serialized bytes."
        )

    return normalized


def _normalize_value(
    value: object,
    *,
    depth: int,
) -> JSONValue:
    if value is None:
        return None

    if type(value) is bool:
        return value

    if type(value) is int:
        return value

    if type(value) is float:
        if not math.isfinite(value):
            raise AuditLogInvalidMetadataError(
                "Audit metadata floating-point values must be finite."
            )

        return value

    if type(value) is str:
        if len(value) > MAX_STRING_VALUE_LENGTH:
            raise AuditLogInvalidMetadataError(
                "Audit metadata string values must contain "
                f"at most {MAX_STRING_VALUE_LENGTH} "
                "characters."
            )

        return value

    if type(value) is list:
        return _normalize_array(
            value,
            depth=depth,
        )

    if type(value) is dict:
        return _normalize_object(
            value,
            depth=depth,
        )

    raise AuditLogInvalidMetadataError("Audit metadata contains a value that is not JSON-native.")


def _normalize_object(
    value: dict[object, object],
    *,
    depth: int,
) -> JSONObject:
    _validate_container_depth(depth)

    if len(value) > MAX_OBJECT_KEYS:
        raise AuditLogInvalidMetadataError(
            f"Audit metadata objects must contain at most {MAX_OBJECT_KEYS} keys."
        )

    normalized: JSONObject = {}
    keys = sorted(_validated_sort_key(key) for key in value)

    for key in keys:
        if len(key) > MAX_OBJECT_KEY_LENGTH:
            raise AuditLogInvalidMetadataError(
                "Audit metadata object keys must contain "
                f"at most {MAX_OBJECT_KEY_LENGTH} "
                "characters."
            )

        normalized[key] = _normalize_value(
            value[key],
            depth=depth + 1,
        )

    return normalized


def _normalize_array(
    value: list[object],
    *,
    depth: int,
) -> list[JSONValue]:
    _validate_container_depth(depth)

    if len(value) > MAX_ARRAY_ITEMS:
        raise AuditLogInvalidMetadataError(
            f"Audit metadata arrays must contain at most {MAX_ARRAY_ITEMS} items."
        )

    return [
        _normalize_value(
            item,
            depth=depth + 1,
        )
        for item in value
    ]


def _validated_sort_key(
    value: object,
) -> str:
    if type(value) is not str:
        raise AuditLogInvalidMetadataError("Audit metadata object keys must be strings.")

    return value


def _validate_container_depth(
    depth: int,
) -> None:
    if depth > MAX_METADATA_DEPTH:
        raise AuditLogInvalidMetadataError(
            f"Audit metadata nesting depth must not exceed {MAX_METADATA_DEPTH}."
        )


__all__ = [
    "MAX_ARRAY_ITEMS",
    "MAX_METADATA_DEPTH",
    "MAX_OBJECT_KEYS",
    "MAX_OBJECT_KEY_LENGTH",
    "MAX_SERIALIZED_METADATA_BYTES",
    "MAX_STRING_VALUE_LENGTH",
    "normalize_audit_metadata",
]
