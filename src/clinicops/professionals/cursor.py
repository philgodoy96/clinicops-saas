import base64
import binascii
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from clinicops.professionals.contracts import ProfessionalCursor
from clinicops.professionals.exceptions import ProfessionalInvalidCursorError

_CURSOR_KEYS = frozenset({"created_at", "professional_id"})


def encode_professional_cursor(cursor: ProfessionalCursor) -> str:
    """Encode a professional keyset position as opaque URL-safe text."""

    payload = {
        "created_at": cursor.created_at.isoformat(),
        "professional_id": str(cursor.professional_id),
    }
    serialized = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")


def decode_professional_cursor(value: str) -> ProfessionalCursor:
    """Decode and validate an opaque professional pagination cursor."""

    try:
        serialized = _decode_urlsafe_base64(value)
        payload: Any = json.loads(serialized.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError

        if frozenset(payload) != _CURSOR_KEYS:
            raise ValueError

        created_at_value = payload["created_at"]
        professional_id_value = payload["professional_id"]
        if not isinstance(created_at_value, str):
            raise ValueError
        if not isinstance(professional_id_value, str):
            raise ValueError

        created_at = datetime.fromisoformat(created_at_value)
        if created_at.tzinfo is None:
            raise ValueError

        professional_id = UUID(professional_id_value)
    except (
        binascii.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ):
        raise ProfessionalInvalidCursorError from None

    return ProfessionalCursor(
        created_at=created_at,
        professional_id=professional_id,
    )


def _decode_urlsafe_base64(value: str) -> bytes:
    if not value:
        raise ValueError

    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        f"{value}{padding}",
        altchars=b"-_",
        validate=True,
    )


__all__ = [
    "decode_professional_cursor",
    "encode_professional_cursor",
]
