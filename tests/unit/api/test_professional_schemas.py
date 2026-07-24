from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinicops.professionals.enums import ProfessionalStatus
from clinicops.professionals.schemas import (
    ProfessionalCreateRequest,
    ProfessionalListResponse,
    ProfessionalMembershipLinkRequest,
    ProfessionalResponse,
    ProfessionalUpdateRequest,
    ProfessionalVersionRequest,
)
from clinicops.professionals.validation import (
    EMAIL_MAX_LENGTH,
    EXTERNAL_REFERENCE_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
    PHONE_MAX_LENGTH,
    REGISTRATION_NUMBER_MAX_LENGTH,
    REGISTRATION_REGION_MAX_LENGTH,
    SPECIALTY_MAX_LENGTH,
)


def test_create_request_accepts_valid_payload() -> None:
    request = ProfessionalCreateRequest(full_name="Morgan Reed")

    assert request.full_name == "Morgan Reed"
    assert request.specialty is None
    assert request.registration_number is None
    assert request.registration_region is None
    assert request.email is None
    assert request.phone is None
    assert request.external_reference is None


def test_create_request_accepts_all_optional_fields() -> None:
    request = ProfessionalCreateRequest(
        full_name="  Morgan Reed  ",
        specialty="  Cardiology  ",
        registration_number="  CRM-12345  ",
        registration_region="  SP  ",
        email="  morgan@example.com  ",
        phone="  +1-202-555-0184  ",
        external_reference="  LEGACY-100  ",
    )

    assert request.full_name == "Morgan Reed"
    assert request.specialty == "Cardiology"
    assert request.registration_number == "CRM-12345"
    assert request.registration_region == "SP"
    assert request.email == "morgan@example.com"
    assert request.phone == "+1-202-555-0184"
    assert request.external_reference == "LEGACY-100"


@pytest.mark.parametrize("value", ["", "   "])
def test_create_request_requires_nonblank_full_name(value: str) -> None:
    with pytest.raises(ValidationError):
        ProfessionalCreateRequest.model_validate({"full_name": value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("full_name", "x" * (FULL_NAME_MAX_LENGTH + 1)),
        ("specialty", ""),
        ("specialty", "x" * (SPECIALTY_MAX_LENGTH + 1)),
        ("registration_number", ""),
        (
            "registration_number",
            "x" * (REGISTRATION_NUMBER_MAX_LENGTH + 1),
        ),
        ("registration_region", ""),
        (
            "registration_region",
            "x" * (REGISTRATION_REGION_MAX_LENGTH + 1),
        ),
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
        "full_name": "Morgan Reed",
        field_name: value,
    }

    with pytest.raises(ValidationError):
        ProfessionalCreateRequest.model_validate(payload)


def test_create_request_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ProfessionalCreateRequest.model_validate(
            {
                "full_name": "Morgan Reed",
                "tenant_id": str(uuid4()),
            }
        )


def test_update_request_distinguishes_omitted_from_explicit_null() -> None:
    omitted = ProfessionalUpdateRequest(expected_version=3)
    explicit_null = ProfessionalUpdateRequest(
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
        ProfessionalUpdateRequest(expected_version=expected_version)


def test_version_request_accepts_positive_version() -> None:
    request = ProfessionalVersionRequest(expected_version=3)

    assert request.expected_version == 3


@pytest.mark.parametrize("expected_version", [0, -1])
def test_version_request_requires_positive_version(
    expected_version: int,
) -> None:
    with pytest.raises(ValidationError):
        ProfessionalVersionRequest(expected_version=expected_version)


def test_version_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProfessionalVersionRequest.model_validate(
            {
                "expected_version": 3,
                "status": "archived",
            }
        )


def test_membership_link_request_requires_membership_and_version() -> None:
    membership_id = uuid4()
    request = ProfessionalMembershipLinkRequest(
        membership_id=membership_id,
        expected_version=2,
    )

    assert request.membership_id == membership_id
    assert request.expected_version == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_version": 2},
        {
            "membership_id": "not-a-uuid",
            "expected_version": 2,
        },
        {
            "membership_id": str(uuid4()),
            "expected_version": 0,
        },
    ],
)
def test_membership_link_request_rejects_invalid_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProfessionalMembershipLinkRequest.model_validate(payload)


def test_professional_response_is_immutable() -> None:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    response = ProfessionalResponse(
        id=uuid4(),
        tenant_id=uuid4(),
        membership_id=None,
        full_name="Morgan Reed",
        specialty="Cardiology",
        registration_number="CRM-12345",
        registration_region="SP",
        email="morgan@example.com",
        phone=None,
        external_reference="LEGACY-100",
        status=ProfessionalStatus.ACTIVE,
        version=3,
        created_at=timestamp,
        updated_at=timestamp,
    )

    with pytest.raises(
        ValidationError,
        match="Instance is frozen",
    ):
        response.full_name = "Changed"


def test_professional_response_includes_nullable_membership_id() -> None:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    membership_id = uuid4()
    linked = ProfessionalResponse(
        id=uuid4(),
        tenant_id=uuid4(),
        membership_id=membership_id,
        full_name="Morgan Reed",
        specialty=None,
        registration_number=None,
        registration_region=None,
        email=None,
        phone=None,
        external_reference=None,
        status=ProfessionalStatus.ACTIVE,
        version=1,
        created_at=timestamp,
        updated_at=timestamp,
    )
    unlinked = ProfessionalResponse(
        id=uuid4(),
        tenant_id=uuid4(),
        membership_id=None,
        full_name="Alex Morgan",
        specialty=None,
        registration_number=None,
        registration_region=None,
        email=None,
        phone=None,
        external_reference=None,
        status=ProfessionalStatus.ACTIVE,
        version=1,
        created_at=timestamp,
        updated_at=timestamp,
    )

    assert linked.membership_id == membership_id
    assert unlinked.membership_id is None
    assert linked.model_dump(mode="json")["membership_id"] == str(membership_id)
    assert unlinked.model_dump(mode="json")["membership_id"] is None


def test_professional_list_response_serializes_public_contract() -> None:
    timestamp = datetime(2026, 7, 23, 21, 30, tzinfo=UTC)
    response = ProfessionalResponse(
        id=uuid4(),
        tenant_id=uuid4(),
        membership_id=None,
        full_name="Morgan Reed",
        specialty="Cardiology",
        registration_number="CRM-12345",
        registration_region="SP",
        email="morgan@example.com",
        phone=None,
        external_reference="LEGACY-100",
        status=ProfessionalStatus.ACTIVE,
        version=3,
        created_at=timestamp,
        updated_at=timestamp,
    )
    page = ProfessionalListResponse(
        items=[response],
        next_cursor="opaque-cursor",
    )
    empty_page = ProfessionalListResponse(
        items=[],
        next_cursor=None,
    )

    payload = page.model_dump(mode="json")

    assert payload["items"][0]["status"] == "active"
    assert payload["items"][0]["full_name"] == "Morgan Reed"
    assert payload["next_cursor"] == "opaque-cursor"
    assert empty_page.next_cursor is None
