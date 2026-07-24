from collections.abc import Callable

import pytest

from clinicops.professionals.enums import ProfessionalMutableField
from clinicops.professionals.exceptions import ProfessionalInvalidUpdateError
from clinicops.professionals.validation import (
    EMAIL_MAX_LENGTH,
    EXTERNAL_REFERENCE_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
    PHONE_MAX_LENGTH,
    REGISTRATION_NUMBER_MAX_LENGTH,
    REGISTRATION_REGION_MAX_LENGTH,
    SEARCH_MAX_LENGTH,
    SPECIALTY_MAX_LENGTH,
    normalize_email,
    normalize_external_reference,
    normalize_full_name,
    normalize_phone,
    normalize_professional_search,
    normalize_registration_number,
    normalize_registration_region,
    normalize_specialty,
    validate_update_fields,
)


def test_normalize_full_name_trims_outer_whitespace() -> None:
    assert normalize_full_name("  Morgan Reed  ") == "Morgan Reed"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
    ],
)
def test_normalize_full_name_rejects_blank_value(value: str) -> None:
    with pytest.raises(ValueError, match="full_name must not be blank"):
        normalize_full_name(value)


def test_normalize_full_name_rejects_oversized_value() -> None:
    with pytest.raises(ValueError, match=str(FULL_NAME_MAX_LENGTH)):
        normalize_full_name("x" * (FULL_NAME_MAX_LENGTH + 1))


@pytest.mark.parametrize(
    ("normalizer", "value", "expected"),
    [
        (normalize_specialty, "  Dentistry  ", "Dentistry"),
        (normalize_registration_number, "  DDS-48291  ", "DDS-48291"),
        (normalize_registration_region, "  ca  ", "CA"),
        (normalize_phone, "  +1-202-555-0130  ", "+1-202-555-0130"),
        (normalize_external_reference, "  PROVIDER-100  ", "PROVIDER-100"),
    ],
)
def test_optional_text_normalizers_trim_values(
    normalizer: Callable[[str | None], str | None],
    value: str,
    expected: str,
) -> None:
    assert normalizer(value) == expected


@pytest.mark.parametrize(
    "normalizer",
    [
        normalize_specialty,
        normalize_registration_number,
        normalize_registration_region,
        normalize_email,
        normalize_phone,
        normalize_external_reference,
    ],
)
@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
    ],
)
def test_optional_text_normalizers_convert_blank_to_none(
    normalizer: Callable[[str | None], str | None],
    value: str | None,
) -> None:
    assert normalizer(value) is None


def test_normalize_email_trims_and_lowercases() -> None:
    assert normalize_email("  MORGAN@EXAMPLE.COM  ") == "morgan@example.com"


@pytest.mark.parametrize(
    "value",
    [
        "missing-at.example.com",
        "missing-domain@",
        "@missing-local.example",
        "contains whitespace@example.com",
    ],
)
def test_normalize_email_rejects_invalid_format_without_echoing_value(
    value: str,
) -> None:
    with pytest.raises(ValueError) as captured:
        normalize_email(value)

    assert str(captured.value) == "email must have a valid format"
    assert value not in str(captured.value)


@pytest.mark.parametrize(
    ("normalizer", "maximum"),
    [
        (normalize_specialty, SPECIALTY_MAX_LENGTH),
        (normalize_registration_number, REGISTRATION_NUMBER_MAX_LENGTH),
        (normalize_registration_region, REGISTRATION_REGION_MAX_LENGTH),
        (normalize_phone, PHONE_MAX_LENGTH),
        (normalize_external_reference, EXTERNAL_REFERENCE_MAX_LENGTH),
    ],
)
def test_optional_text_normalizers_reject_oversized_values(
    normalizer: Callable[[str | None], str | None],
    maximum: int,
) -> None:
    with pytest.raises(ValueError, match=str(maximum)):
        normalizer("x" * (maximum + 1))


def test_normalize_email_rejects_oversized_value() -> None:
    local_part = "x" * EMAIL_MAX_LENGTH

    with pytest.raises(ValueError, match="valid format"):
        normalize_email(f"{local_part}@example.com")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("  Morgan  ", "Morgan"),
    ],
)
def test_normalize_professional_search(
    value: str | None,
    expected: str | None,
) -> None:
    assert normalize_professional_search(value) == expected


def test_normalize_professional_search_rejects_oversized_value() -> None:
    with pytest.raises(ValueError, match=str(SEARCH_MAX_LENGTH)):
        normalize_professional_search("x" * (SEARCH_MAX_LENGTH + 1))


def test_validate_update_fields_rejects_empty_patch() -> None:
    with pytest.raises(ProfessionalInvalidUpdateError):
        validate_update_fields(frozenset())


def test_validate_update_fields_preserves_explicit_fields() -> None:
    fields = frozenset(
        {
            ProfessionalMutableField.EMAIL,
            ProfessionalMutableField.SPECIALTY,
        }
    )

    assert validate_update_fields(fields) is fields
