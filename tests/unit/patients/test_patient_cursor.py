import base64
import json
import string
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.patients.contracts import PatientCursor
from clinicops.patients.cursor import (
    decode_patient_cursor,
    encode_patient_cursor,
)
from clinicops.patients.exceptions import PatientInvalidCursorError


def _raw_cursor(payload: object) -> str:
    serialized = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")


def test_patient_cursor_round_trip() -> None:
    cursor = PatientCursor(
        created_at=datetime(2026, 7, 23, 21, 30, 45, 123456, tzinfo=UTC),
        patient_id=uuid4(),
    )

    encoded = encode_patient_cursor(cursor)
    decoded = decode_patient_cursor(encoded)

    assert decoded == cursor


def test_patient_cursor_is_url_safe_and_contains_no_tenant_or_pii() -> None:
    cursor = PatientCursor(
        created_at=datetime(2026, 7, 23, 21, 30, tzinfo=UTC),
        patient_id=uuid4(),
    )

    encoded = encode_patient_cursor(cursor)
    allowed_characters = set(string.ascii_letters + string.digits + "-_")

    assert set(encoded) <= allowed_characters

    padding = "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"))

    assert payload == {
        "created_at": cursor.created_at.isoformat(),
        "patient_id": str(cursor.patient_id),
    }


def test_patient_cursor_encoding_is_deterministic() -> None:
    cursor = PatientCursor(
        created_at=datetime(2026, 7, 23, 21, 30, tzinfo=UTC),
        patient_id=uuid4(),
    )

    assert encode_patient_cursor(cursor) == encode_patient_cursor(cursor)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-valid-base64!",
        _raw_cursor([]),
        _raw_cursor({"created_at": "2026-07-23T21:30:00+00:00"}),
        _raw_cursor(
            {
                "created_at": "2026-07-23T21:30:00+00:00",
                "patient_id": str(uuid4()),
                "tenant_id": str(uuid4()),
            }
        ),
        _raw_cursor(
            {
                "created_at": "not-a-datetime",
                "patient_id": str(uuid4()),
            }
        ),
        _raw_cursor(
            {
                "created_at": "2026-07-23T21:30:00+00:00",
                "patient_id": "not-a-uuid",
            }
        ),
        _raw_cursor(
            {
                "created_at": "2026-07-23T21:30:00",
                "patient_id": str(uuid4()),
            }
        ),
    ],
)
def test_invalid_patient_cursor_is_rejected(value: str) -> None:
    with pytest.raises(PatientInvalidCursorError):
        decode_patient_cursor(value)


def test_naive_datetime_cannot_be_encoded() -> None:
    cursor = PatientCursor(
        created_at=datetime(2026, 7, 23, 21, 30),
        patient_id=uuid4(),
    )

    with pytest.raises(PatientInvalidCursorError):
        encode_patient_cursor(cursor)
