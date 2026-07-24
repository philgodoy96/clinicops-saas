import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from clinicops.patients.contracts import PatientCursor
from clinicops.patients.exceptions import PatientInvalidCursorError

_CURSOR_FIELDS = frozenset({"created_at", "patient_id"})


def encode_patient_cursor(cursor: PatientCursor) -> str:
    """Encode a stable patient keyset position as opaque URL-safe text."""

    if cursor.created_at.utcoffset() is None:
        raise PatientInvalidCursorError

    payload = {
        "created_at": cursor.created_at.isoformat(),
        "patient_id": str(cursor.patient_id),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")


def decode_patient_cursor(value: str) -> PatientCursor:
    """Decode and validate an opaque patient pagination cursor."""

    try:
        payload = _decode_payload(value)
        if set(payload) != _CURSOR_FIELDS:
            raise PatientInvalidCursorError

        created_at_value = payload["created_at"]
        patient_id_value = payload["patient_id"]
        if not isinstance(created_at_value, str):
            raise PatientInvalidCursorError
        if not isinstance(patient_id_value, str):
            raise PatientInvalidCursorError

        created_at = datetime.fromisoformat(created_at_value)
        if created_at.utcoffset() is None:
            raise PatientInvalidCursorError

        return PatientCursor(
            created_at=created_at,
            patient_id=UUID(patient_id_value),
        )
    except PatientInvalidCursorError:
        raise
    except ValueError:
        raise PatientInvalidCursorError from None


def _decode_payload(value: str) -> dict[str, Any]:
    if not value:
        raise PatientInvalidCursorError

    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(
        f"{value}{padding}",
        altchars=b"-_",
        validate=True,
    )
    payload = json.loads(decoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise PatientInvalidCursorError

    return payload


__all__ = ["decode_patient_cursor", "encode_patient_cursor"]
