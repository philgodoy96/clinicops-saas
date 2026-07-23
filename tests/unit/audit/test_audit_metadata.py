from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from clinicops.audit.exceptions import (
    AuditLogInvalidMetadataError,
)
from clinicops.audit.metadata import (
    MAX_ARRAY_ITEMS,
    MAX_METADATA_DEPTH,
    MAX_OBJECT_KEY_LENGTH,
    MAX_OBJECT_KEYS,
    MAX_STRING_VALUE_LENGTH,
    normalize_audit_metadata,
)


class UnsupportedMetadataValue:
    pass


def test_empty_metadata_is_valid() -> None:
    assert normalize_audit_metadata({}) == {}


def test_metadata_accepts_json_native_values() -> None:
    metadata = {
        "none": None,
        "boolean": True,
        "integer": 42,
        "float": 3.5,
        "string": "value",
        "array": [
            None,
            False,
            7,
            1.25,
            "nested",
        ],
        "object": {
            "enabled": True,
        },
    }

    normalized = normalize_audit_metadata(metadata)

    assert normalized == metadata


def test_metadata_is_normalized_deterministically() -> None:
    metadata = {
        "z": {
            "second": 2,
            "first": 1,
        },
        "a": "value",
        "m": [
            {
                "beta": True,
                "alpha": False,
            }
        ],
    }

    normalized = normalize_audit_metadata(metadata)

    assert list(normalized) == [
        "a",
        "m",
        "z",
    ]

    nested_z = normalized["z"]
    assert isinstance(nested_z, dict)
    assert list(nested_z) == [
        "first",
        "second",
    ]

    nested_m = normalized["m"]
    assert isinstance(nested_m, list)
    nested_m_item = nested_m[0]
    assert isinstance(nested_m_item, dict)
    assert list(nested_m_item) == [
        "alpha",
        "beta",
    ]


def test_metadata_normalization_does_not_mutate_input() -> None:
    metadata = {
        "z": {
            "second": 2,
            "first": 1,
        },
    }

    normalized = normalize_audit_metadata(metadata)

    assert list(metadata) == ["z"]
    assert list(metadata["z"]) == [
        "second",
        "first",
    ]
    assert normalized is not metadata
    assert normalized["z"] is not metadata["z"]


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        "value",
        42,
        True,
    ],
)
def test_metadata_requires_json_object(
    metadata: object,
) -> None:
    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="must be a JSON object",
    ):
        normalize_audit_metadata(metadata)


def test_metadata_rejects_non_string_object_key() -> None:
    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="keys must be strings",
    ):
        normalize_audit_metadata(
            {
                1: "value",
            }
        )


def test_metadata_rejects_oversized_object_key() -> None:
    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="object keys",
    ):
        normalize_audit_metadata(
            {
                "k" * (MAX_OBJECT_KEY_LENGTH + 1): "value",
            }
        )


def test_metadata_accepts_maximum_object_size() -> None:
    metadata = {f"key-{index}": index for index in range(MAX_OBJECT_KEYS)}

    normalized = normalize_audit_metadata(metadata)

    assert len(normalized) == MAX_OBJECT_KEYS


def test_metadata_rejects_oversized_object() -> None:
    metadata = {f"key-{index}": index for index in range(MAX_OBJECT_KEYS + 1)}

    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="objects must contain at most",
    ):
        normalize_audit_metadata(metadata)


def test_metadata_accepts_maximum_array_size() -> None:
    metadata = {
        "items": list(range(MAX_ARRAY_ITEMS)),
    }

    normalized = normalize_audit_metadata(metadata)

    assert (
        len(
            normalized["items"]  # type: ignore[arg-type]
        )
        == MAX_ARRAY_ITEMS
    )


def test_metadata_rejects_oversized_array() -> None:
    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="arrays must contain at most",
    ):
        normalize_audit_metadata(
            {
                "items": list(range(MAX_ARRAY_ITEMS + 1)),
            }
        )


def test_metadata_accepts_maximum_string_length() -> None:
    value = "x" * MAX_STRING_VALUE_LENGTH

    normalized = normalize_audit_metadata(
        {
            "value": value,
        }
    )

    assert normalized["value"] == value


def test_metadata_rejects_oversized_string() -> None:
    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="string values",
    ):
        normalize_audit_metadata(
            {
                "value": ("x" * (MAX_STRING_VALUE_LENGTH + 1)),
            }
        )


def test_metadata_accepts_maximum_nesting_depth() -> None:
    metadata: dict[str, object] = {
        "level_1": {
            "level_2": {
                "level_3": {
                    "level_4": {
                        "level_5": {},
                    },
                },
            },
        },
    }

    normalized = normalize_audit_metadata(metadata)

    assert normalized == metadata
    assert MAX_METADATA_DEPTH == 6


def test_metadata_rejects_excessive_nesting_depth() -> None:
    metadata: dict[str, object] = {
        "level_1": {
            "level_2": {
                "level_3": {
                    "level_4": {
                        "level_5": {
                            "level_6": {},
                        },
                    },
                },
            },
        },
    }

    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="nesting depth",
    ):
        normalize_audit_metadata(metadata)


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_metadata_rejects_non_finite_float(
    value: float,
) -> None:
    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="must be finite",
    ):
        normalize_audit_metadata(
            {
                "value": value,
            }
        )


@pytest.mark.parametrize(
    "value",
    [
        uuid4(),
        datetime.now(UTC),
        Decimal("10.50"),
        b"bytes",
        {"set-value"},
        ("tuple-value",),
        UnsupportedMetadataValue(),
    ],
)
def test_metadata_rejects_non_json_native_value(
    value: object,
) -> None:
    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="not JSON-native",
    ):
        normalize_audit_metadata(
            {
                "value": value,
            }
        )


def test_metadata_rejects_oversized_serialized_payload() -> None:
    metadata = {f"field-{index}": ("x" * MAX_STRING_VALUE_LENGTH) for index in range(10)}

    with pytest.raises(
        AuditLogInvalidMetadataError,
        match="serialized bytes",
    ):
        normalize_audit_metadata(metadata)
