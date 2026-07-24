from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinicops.patients.enums import PatientStatus
from clinicops.patients.schemas import (
    PatientCreateRequest,
    PatientListResponse,
    PatientResponse,
    PatientUpdateRequest,
    PatientVersionRequest,
)
from clinicops.patients.validation import (
    EMAIL_MAX_LENGTH,
    EXTERNAL_REFERENCE_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
    PHONE_MAX_LENGTH,
)


def test_create_request_trims_bounded_strings() -> None:
    request = PatientCreateRequest(
        full_name="  Jordan Lee  ",
        email="  jordan@example.com  ",
        phone="  +1-202-555-0184  ",
        external_reference="  LEGACY-100  ",
    )

    assert request.full_name == "Jordan Lee"
    assert request.email == "jordan@example.com"
    assert request.phone == "+1-202-555-0184"
    assert request.external_reference == "LEGACY-100"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("full_name", ""),
        ("full_name", "x" * (FULL_NAME_MAX_LENGTH + 1)),
        ("email", "invalid-email"),
        ("email", "x" * (EMAIL_MAX_LENGTH + 1)),
        ("phone", ""),
        ("phone", "1" * (PHONE_MAX_LENGTH + 1)),
        ("external_reference", ""),
        (
            "external_reference",
            "x" * (EXTERNAL_REFERENCE_MAX_LENGTH + 1),
        ),
    ],
)
def test_create_request_rejects_invalid_bounded_fields(
    field_name: str,
    value: str,
) -> None:
    payload: dict[str, object] = {
        "full_name": "Jordan Lee",
        field_name: value,
    }

    with pytest.raises(ValidationError):
        PatientCreateRequest.model_validate(payload)


def test_create_request_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PatientCreateRequest.model_validate(
            {
                "full_name": "Jordan Lee",
                "tenant_id": str(uuid4()),
            }
        )


def test_update_request_distinguishes_omitted_from_explicit_null() -> None:
    omitted = PatientUpdateRequest(expected_version=3)
    explicit_null = PatientUpdateRequest(
        expected_version=3,
        email=None,
    )

    assert omitted.model_fields_set == {"expected_version"}
    assert explicit_null.model_fields_set == {
        "expected_version",
        "email",
    }
    assert omitted.email is None
    assert explicit_null.email is None


@pytest.mark.parametrize("expected_version", [0, -1])
def test_update_request_requires_positive_version(
    expected_version: int,
) -> None:
    with pytest.raises(ValidationError):
        PatientUpdateRequest(expected_version=expected_version)


def test_version_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PatientVersionRequest.model_validate(
            {
                "expected_version": 3,
                "status": "archived",
            }
        )


def test_patient_response_serializes_public_contract() -> None:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    response = PatientResponse(
        id=uuid4(),
        tenant_id=uuid4(),
        full_name="Jordan Lee",
        date_of_birth=date(1992, 8, 14),
        email="jordan@example.com",
        phone=None,
        external_reference="LEGACY-100",
        status=PatientStatus.ACTIVE,
        version=3,
        created_at=timestamp,
        updated_at=timestamp,
    )
    page = PatientListResponse(
        items=[response],
        next_cursor="opaque-cursor",
    )

    payload = page.model_dump(mode="json")

    assert payload["items"][0]["status"] == "active"
    assert payload["items"][0]["date_of_birth"] == "1992-08-14"
    assert payload["next_cursor"] == "opaque-cursor"
