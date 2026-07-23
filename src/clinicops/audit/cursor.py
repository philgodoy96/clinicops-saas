import base64
import binascii
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from clinicops.audit.contracts import AuditLogCursor
from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)

MAX_AUDIT_CURSOR_TOKEN_LENGTH = 1_024
MAX_AUDIT_CURSOR_PAYLOAD_BYTES = 512

_EXPECTED_CURSOR_FIELDS = {
    "audit_log_id",
    "recorded_at",
}


def encode_audit_log_cursor(
    cursor: AuditLogCursor,
) -> str:
    if not isinstance(cursor, AuditLogCursor):
        raise AuditLogInvalidConfigurationError("cursor must be an AuditLogCursor.")

    _validate_aware_datetime(cursor.recorded_at)

    if not isinstance(cursor.audit_log_id, UUID):
        raise AuditLogInvalidConfigurationError("cursor audit_log_id must be a UUID.")

    payload = {
        "audit_log_id": str(cursor.audit_log_id),
        "recorded_at": cursor.recorded_at.isoformat(),
    }

    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    if len(serialized) > MAX_AUDIT_CURSOR_PAYLOAD_BYTES:
        raise AuditLogInvalidConfigurationError("The encoded audit cursor payload is too large.")

    token = base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")

    if len(token) > MAX_AUDIT_CURSOR_TOKEN_LENGTH:
        raise AuditLogInvalidConfigurationError("The encoded audit cursor token is too large.")

    return token


def decode_audit_log_cursor(
    token: str,
) -> AuditLogCursor:
    normalized_token = _validate_token(token)
    payload_bytes = _decode_token(normalized_token)

    if len(payload_bytes) > MAX_AUDIT_CURSOR_PAYLOAD_BYTES:
        raise AuditLogInvalidConfigurationError("The audit cursor payload is too large.")

    payload = _load_payload(payload_bytes)

    if set(payload) != _EXPECTED_CURSOR_FIELDS:
        raise AuditLogInvalidConfigurationError(
            "The audit cursor must contain exactly audit_log_id and recorded_at."
        )

    recorded_at = _parse_recorded_at(payload["recorded_at"])
    audit_log_id = _parse_audit_log_id(payload["audit_log_id"])

    return AuditLogCursor(
        recorded_at=recorded_at,
        audit_log_id=audit_log_id,
    )


def _validate_token(
    token: object,
) -> str:
    if not isinstance(token, str):
        raise AuditLogInvalidConfigurationError("The audit cursor token must be a string.")

    if token != token.strip():
        raise AuditLogInvalidConfigurationError(
            "The audit cursor token must not contain surrounding whitespace."
        )

    if not token:
        raise AuditLogInvalidConfigurationError("The audit cursor token must not be empty.")

    if len(token) > MAX_AUDIT_CURSOR_TOKEN_LENGTH:
        raise AuditLogInvalidConfigurationError("The audit cursor token is too large.")

    return token


def _decode_token(
    token: str,
) -> bytes:
    padding = "=" * (-len(token) % 4)

    try:
        return base64.b64decode(
            token + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise AuditLogInvalidConfigurationError(
            "The audit cursor is not valid URL-safe Base64."
        ) from exc


def _load_payload(
    payload_bytes: bytes,
) -> dict[str, Any]:
    try:
        payload_text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditLogInvalidConfigurationError(
            "The audit cursor payload is not valid UTF-8."
        ) from exc

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise AuditLogInvalidConfigurationError(
            "The audit cursor payload is not valid JSON."
        ) from exc

    if type(payload) is not dict:
        raise AuditLogInvalidConfigurationError("The audit cursor payload must be a JSON object.")

    if not all(type(key) is str for key in payload):
        raise AuditLogInvalidConfigurationError("The audit cursor payload keys must be strings.")

    return payload


def _parse_recorded_at(
    value: object,
) -> datetime:
    if not isinstance(value, str):
        raise AuditLogInvalidConfigurationError(
            "The audit cursor recorded_at value must be a string."
        )

    try:
        recorded_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AuditLogInvalidConfigurationError(
            "The audit cursor recorded_at value is not a valid ISO-8601 timestamp."
        ) from exc

    _validate_aware_datetime(recorded_at)

    return recorded_at


def _validate_aware_datetime(
    value: object,
) -> None:
    if not isinstance(value, datetime):
        raise AuditLogInvalidConfigurationError(
            "The audit cursor recorded_at value must be a datetime."
        )

    if value.tzinfo is None or value.utcoffset() is None:
        raise AuditLogInvalidConfigurationError(
            "The audit cursor recorded_at value must be timezone-aware."
        )


def _parse_audit_log_id(
    value: object,
) -> UUID:
    if not isinstance(value, str):
        raise AuditLogInvalidConfigurationError(
            "The audit cursor audit_log_id value must be a string."
        )

    try:
        audit_log_id = UUID(value)
    except ValueError as exc:
        raise AuditLogInvalidConfigurationError(
            "The audit cursor audit_log_id value is not a valid UUID."
        ) from exc

    if str(audit_log_id) != value.lower():
        raise AuditLogInvalidConfigurationError(
            "The audit cursor audit_log_id value must use canonical UUID format."
        )

    return audit_log_id


__all__ = [
    "MAX_AUDIT_CURSOR_PAYLOAD_BYTES",
    "MAX_AUDIT_CURSOR_TOKEN_LENGTH",
    "decode_audit_log_cursor",
    "encode_audit_log_cursor",
]
