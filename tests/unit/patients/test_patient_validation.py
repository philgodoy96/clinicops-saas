from datetime import date, timedelta

import pytest

from clinicops.patients.enums import PatientMutableField
from clinicops.patients.exceptions import (
    PatientInvalidDateOfBirthError,
    PatientInvalidUpdateError,
)
from clinicops.patients.validation import (
    EMAIL_MAX_LENGTH,
    EXTERNAL_REFERENCE_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
    PHONE_MAX_LENGTH,
    SEARCH_MAX_LENGTH,
    normalize_email,
    normalize_external_reference,
    normalize_full_name,
    normalize_patient_search,
    normalize_phone,
    validate_date_of_birth,
    validate_update_fields,
)


def test_full_name_is_trimmed_without_collapsing_internal_spacing() -> None:
    assert normalize_full_name("  Jordan  Lee  ") == "Jordan  Lee"


@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_full_name_rejects_blank_values(value: str) -> None:
    with pytest.raises(ValueError, match="full_name must not be blank"):
        normalize_full_name(value)


def test_full_name_rejects_values_above_limit() -> None:
    with pytest.raises(ValueError, match=str(FULL_NAME_MAX_LENGTH)):
        normalize_full_name("a" * (FULL_NAME_MAX_LENGTH + 1))


def test_email_is_trimmed_and_lowercased() -> None:
    assert normalize_email("  Jordan.Lee@Example.COM  ") == "jordan.lee@example.com"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "missing-at.example.com",
        "missing-domain@",
        "@missing-local.example.com",
    ],
)
def test_email_rejects_blank_or_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_email(value)


def test_email_allows_null() -> None:
    assert normalize_email(None) is None


def test_email_rejects_values_above_limit_without_echoing_value() -> None:
    value = f"{'a' * EMAIL_MAX_LENGTH}@example.com"

    with pytest.raises(ValueError) as error:
        normalize_email(value)

    assert value not in str(error.value)


def test_phone_is_trimmed_and_allows_international_representation() -> None:
    assert normalize_phone("  +55 (51) 99999-0000  ") == "+55 (51) 99999-0000"


def test_phone_allows_null() -> None:
    assert normalize_phone(None) is None


def test_phone_rejects_blank_and_over_limit_values() -> None:
    with pytest.raises(ValueError, match="phone must not be blank"):
        normalize_phone(" ")

    with pytest.raises(ValueError, match=str(PHONE_MAX_LENGTH)):
        normalize_phone("1" * (PHONE_MAX_LENGTH + 1))


def test_external_reference_is_trimmed_and_remains_case_sensitive() -> None:
    assert normalize_external_reference("  Legacy-AbC-10  ") == "Legacy-AbC-10"


def test_external_reference_allows_null() -> None:
    assert normalize_external_reference(None) is None


def test_external_reference_rejects_blank_and_over_limit_values() -> None:
    with pytest.raises(
        ValueError,
        match="external_reference must not be blank",
    ):
        normalize_external_reference(" ")

    with pytest.raises(ValueError, match=str(EXTERNAL_REFERENCE_MAX_LENGTH)):
        normalize_external_reference("x" * (EXTERNAL_REFERENCE_MAX_LENGTH + 1))


def test_date_of_birth_allows_null_and_non_future_dates() -> None:
    today = date(2026, 7, 23)

    assert validate_date_of_birth(None, today=today) is None
    assert validate_date_of_birth(today, today=today) == today
    assert validate_date_of_birth(date(1992, 8, 14), today=today) == date(1992, 8, 14)


def test_date_of_birth_rejects_future_date_without_echoing_value() -> None:
    today = date(2026, 7, 23)
    future_date = today + timedelta(days=1)

    with pytest.raises(PatientInvalidDateOfBirthError) as error:
        validate_date_of_birth(future_date, today=today)

    assert future_date.isoformat() not in str(error.value)


def test_search_is_trimmed_and_blank_search_becomes_null() -> None:
    assert normalize_patient_search("  Jordan Lee  ") == "Jordan Lee"
    assert normalize_patient_search("   ") is None
    assert normalize_patient_search(None) is None


def test_search_rejects_values_above_limit() -> None:
    with pytest.raises(ValueError, match=str(SEARCH_MAX_LENGTH)):
        normalize_patient_search("s" * (SEARCH_MAX_LENGTH + 1))


def test_update_fields_require_at_least_one_explicit_field() -> None:
    with pytest.raises(PatientInvalidUpdateError):
        validate_update_fields(frozenset())


def test_update_fields_preserve_explicit_field_set() -> None:
    fields = frozenset(
        {
            PatientMutableField.EMAIL,
            PatientMutableField.PHONE,
        }
    )

    assert validate_update_fields(fields) is fields
