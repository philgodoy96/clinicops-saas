import base64
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.professionals.contracts import ProfessionalCursor
from clinicops.professionals.cursor import (
    decode_professional_cursor,
    encode_professional_cursor,
)
from clinicops.professionals.exceptions import ProfessionalInvalidCursorError


def _encoded_payload(payload: object) -> str:
    serialized = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")


def test_professional_cursor_round_trip() -> None:
    cursor = ProfessionalCursor(
        created_at=datetime(2026, 7, 24, 12, 30, tzinfo=UTC),
        professional_id=uuid4(),
    )

    encoded = encode_professional_cursor(cursor)

    assert decode_professional_cursor(encoded) == cursor


def test_professional_cursor_encoding_is_deterministic_and_url_safe() -> None:
    cursor = ProfessionalCursor(
        created_at=datetime(2026, 7, 24, 12, 30, tzinfo=UTC),
        professional_id=uuid4(),
    )

    first = encode_professional_cursor(cursor)
    second = encode_professional_cursor(cursor)

    assert first == second
    assert "=" not in first
    assert "+" not in first
    assert "/" not in first


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-base64!",
        _encoded_payload([]),
        _encoded_payload({}),
        _encoded_payload(
            {
                "created_at": "2026-07-24T12:30:00+00:00",
            }
        ),
        _encoded_payload(
            {
                "created_at": "2026-07-24T12:30:00+00:00",
                "professional_id": str(uuid4()),
                "unexpected": True,
            }
        ),
        _encoded_payload(
            {
                "created_at": "not-a-timestamp",
                "professional_id": str(uuid4()),
            }
        ),
        _encoded_payload(
            {
                "created_at": "2026-07-24T12:30:00+00:00",
                "professional_id": "not-a-uuid",
            }
        ),
        _encoded_payload(
            {
                "created_at": "2026-07-24T12:30:00",
                "professional_id": str(uuid4()),
            }
        ),
    ],
)
def test_decode_rejects_malformed_professional_cursor(value: str) -> None:
    with pytest.raises(ProfessionalInvalidCursorError):
        decode_professional_cursor(value)


def test_professional_cursor_payload_contains_no_tenant_or_profile_data() -> None:
    cursor = ProfessionalCursor(
        created_at=datetime(2026, 7, 24, 12, 30, tzinfo=UTC),
        professional_id=uuid4(),
    )
    encoded = encode_professional_cursor(cursor)
    padding = "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"))

    assert payload == {
        "created_at": cursor.created_at.isoformat(),
        "professional_id": str(cursor.professional_id),
    }
