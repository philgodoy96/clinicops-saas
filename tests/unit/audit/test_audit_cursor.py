import base64
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import uuid4

import pytest

from clinicops.audit.contracts import AuditLogCursor
from clinicops.audit.cursor import (
    MAX_AUDIT_CURSOR_TOKEN_LENGTH,
    decode_audit_log_cursor,
    encode_audit_log_cursor,
)
from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)


def _encode_raw_payload(
    payload: object,
) -> str:
    serialized = json.dumps(
        payload,
        separators=(",", ":"),
    ).encode("utf-8")

    return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")


def _encode_raw_bytes(
    value: bytes,
) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_cursor_round_trip_preserves_values() -> None:
    cursor = AuditLogCursor(
        recorded_at=datetime(
            2026,
            7,
            23,
            12,
            30,
            45,
            123456,
            tzinfo=UTC,
        ),
        audit_log_id=uuid4(),
    )

    token = encode_audit_log_cursor(cursor)
    decoded = decode_audit_log_cursor(token)

    assert decoded == cursor


def test_cursor_round_trip_preserves_timezone_offset() -> None:
    offset = timezone(timedelta(hours=-3))
    cursor = AuditLogCursor(
        recorded_at=datetime(
            2026,
            7,
            23,
            9,
            15,
            tzinfo=offset,
        ),
        audit_log_id=uuid4(),
    )

    decoded = decode_audit_log_cursor(encode_audit_log_cursor(cursor))

    assert decoded.recorded_at.isoformat() == cursor.recorded_at.isoformat()
    assert decoded.recorded_at.utcoffset() == timedelta(hours=-3)


def test_encoded_cursor_is_url_safe_and_unpadded() -> None:
    cursor = AuditLogCursor(
        recorded_at=datetime.now(UTC),
        audit_log_id=uuid4(),
    )

    token = encode_audit_log_cursor(cursor)

    assert "+" not in token
    assert "/" not in token
    assert "=" not in token
    assert token
    assert token.isascii()


def test_equivalent_cursor_has_deterministic_token() -> None:
    cursor = AuditLogCursor(
        recorded_at=datetime(
            2026,
            7,
            23,
            12,
            0,
            tzinfo=UTC,
        ),
        audit_log_id=uuid4(),
    )

    first_token = encode_audit_log_cursor(cursor)
    second_token = encode_audit_log_cursor(cursor)

    assert first_token == second_token


def test_encoder_requires_cursor_contract() -> None:
    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="must be an AuditLogCursor",
    ):
        encode_audit_log_cursor(cast(AuditLogCursor, object()))


def test_encoder_rejects_naive_timestamp() -> None:
    cursor = AuditLogCursor(
        recorded_at=datetime(
            2026,
            7,
            23,
            12,
            0,
        ),
        audit_log_id=uuid4(),
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="timezone-aware",
    ):
        encode_audit_log_cursor(cursor)


@pytest.mark.parametrize(
    "token",
    [
        "",
        " ",
        "\t",
        "\n",
        " token",
        "token ",
    ],
)
def test_decoder_rejects_empty_or_padded_whitespace(
    token: str,
) -> None:
    with pytest.raises(
        AuditLogInvalidConfigurationError,
    ):
        decode_audit_log_cursor(token)


def test_decoder_requires_string_token() -> None:
    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="must be a string",
    ):
        decode_audit_log_cursor(cast(str, object()))


def test_decoder_rejects_oversized_token() -> None:
    token = "a" * (MAX_AUDIT_CURSOR_TOKEN_LENGTH + 1)

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="token is too large",
    ):
        decode_audit_log_cursor(token)


@pytest.mark.parametrize(
    "token",
    [
        "not*valid",
        "%%%%",
        "abc$",
    ],
)
def test_decoder_rejects_invalid_base64(
    token: str,
) -> None:
    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="URL-safe Base64",
    ):
        decode_audit_log_cursor(token)


def test_decoder_rejects_invalid_utf8() -> None:
    token = _encode_raw_bytes(b"\xff\xfe\xfd")

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="valid UTF-8",
    ):
        decode_audit_log_cursor(token)


def test_decoder_rejects_invalid_json() -> None:
    token = _encode_raw_bytes(b"{not-json}")

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="valid JSON",
    ):
        decode_audit_log_cursor(token)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "cursor",
        42,
        True,
    ],
)
def test_decoder_requires_json_object(
    payload: object,
) -> None:
    token = _encode_raw_payload(payload)

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="JSON object",
    ):
        decode_audit_log_cursor(token)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "recorded_at": ("2026-07-23T12:00:00+00:00"),
        },
        {
            "audit_log_id": str(uuid4()),
        },
        {},
    ],
)
def test_decoder_rejects_missing_fields(
    payload: dict[str, object],
) -> None:
    token = _encode_raw_payload(payload)

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="contain exactly",
    ):
        decode_audit_log_cursor(token)


def test_decoder_rejects_unexpected_fields() -> None:
    token = _encode_raw_payload(
        {
            "audit_log_id": str(uuid4()),
            "recorded_at": ("2026-07-23T12:00:00+00:00"),
            "tenant_id": str(uuid4()),
        }
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="contain exactly",
    ):
        decode_audit_log_cursor(token)


@pytest.mark.parametrize(
    "recorded_at",
    [
        None,
        123,
        True,
        [],
        {},
    ],
)
def test_decoder_rejects_non_string_timestamp(
    recorded_at: object,
) -> None:
    token = _encode_raw_payload(
        {
            "audit_log_id": str(uuid4()),
            "recorded_at": recorded_at,
        }
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="recorded_at value must be a string",
    ):
        decode_audit_log_cursor(token)


@pytest.mark.parametrize(
    "recorded_at",
    [
        "",
        "not-a-timestamp",
        "2026-99-99T12:00:00+00:00",
    ],
)
def test_decoder_rejects_invalid_timestamp(
    recorded_at: str,
) -> None:
    token = _encode_raw_payload(
        {
            "audit_log_id": str(uuid4()),
            "recorded_at": recorded_at,
        }
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="valid ISO-8601",
    ):
        decode_audit_log_cursor(token)


def test_decoder_rejects_naive_timestamp() -> None:
    token = _encode_raw_payload(
        {
            "audit_log_id": str(uuid4()),
            "recorded_at": ("2026-07-23T12:00:00"),
        }
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="timezone-aware",
    ):
        decode_audit_log_cursor(token)


@pytest.mark.parametrize(
    "audit_log_id",
    [
        None,
        123,
        True,
        [],
        {},
    ],
)
def test_decoder_rejects_non_string_uuid(
    audit_log_id: object,
) -> None:
    token = _encode_raw_payload(
        {
            "audit_log_id": audit_log_id,
            "recorded_at": ("2026-07-23T12:00:00+00:00"),
        }
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="audit_log_id value must be a string",
    ):
        decode_audit_log_cursor(token)


def test_decoder_rejects_invalid_uuid() -> None:
    token = _encode_raw_payload(
        {
            "audit_log_id": "not-a-uuid",
            "recorded_at": ("2026-07-23T12:00:00+00:00"),
        }
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="valid UUID",
    ):
        decode_audit_log_cursor(token)


def test_decoder_rejects_noncanonical_uuid() -> None:
    audit_log_id = uuid4()
    token = _encode_raw_payload(
        {
            "audit_log_id": audit_log_id.hex,
            "recorded_at": ("2026-07-23T12:00:00+00:00"),
        }
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="canonical UUID format",
    ):
        decode_audit_log_cursor(token)
