from datetime import date
from typing import Annotated

from pydantic import StringConstraints, TypeAdapter, ValidationError

from clinicops.patients.enums import PatientMutableField
from clinicops.patients.exceptions import (
    PatientInvalidDateOfBirthError,
    PatientInvalidUpdateError,
)

FULL_NAME_MAX_LENGTH = 200
EMAIL_MAX_LENGTH = 320
PHONE_MAX_LENGTH = 50
EXTERNAL_REFERENCE_MAX_LENGTH = 100
SEARCH_MAX_LENGTH = 100

EmailText = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=EMAIL_MAX_LENGTH,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]

_email_adapter = TypeAdapter(EmailText)


def normalize_full_name(value: str) -> str:
    """Normalize and validate a required patient full name."""

    normalized = value.strip()
    if not normalized:
        raise ValueError("full_name must not be blank")
    if len(normalized) > FULL_NAME_MAX_LENGTH:
        raise ValueError(f"full_name must be at most {FULL_NAME_MAX_LENGTH} characters")
    return normalized


def normalize_email(value: str | None) -> str | None:
    """Normalize an optional patient email without exposing its value."""

    if value is None:
        return None

    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("email must not be blank")

    try:
        return _email_adapter.validate_python(normalized)
    except ValidationError:
        raise ValueError("email must have a valid format") from None


def normalize_phone(value: str | None) -> str | None:
    """Normalize an optional patient phone representation."""

    return _normalize_optional_text(
        value,
        field_name="phone",
        max_length=PHONE_MAX_LENGTH,
    )


def normalize_external_reference(value: str | None) -> str | None:
    """Normalize an optional tenant-owned external reference."""

    return _normalize_optional_text(
        value,
        field_name="external_reference",
        max_length=EXTERNAL_REFERENCE_MAX_LENGTH,
    )


def validate_date_of_birth(
    value: date | None,
    *,
    today: date | None = None,
) -> date | None:
    """Reject a patient date of birth that is in the future."""

    if value is None:
        return None

    comparison_date = today or date.today()
    if value > comparison_date:
        raise PatientInvalidDateOfBirthError

    return value


def normalize_patient_search(value: str | None) -> str | None:
    """Normalize an optional bounded patient search term."""

    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > SEARCH_MAX_LENGTH:
        raise ValueError(f"search must be at most {SEARCH_MAX_LENGTH} characters")
    return normalized


def validate_update_fields(
    fields_to_update: frozenset[PatientMutableField],
) -> frozenset[PatientMutableField]:
    """Require at least one explicitly supplied mutable field."""

    if not fields_to_update:
        raise PatientInvalidUpdateError

    return fields_to_update


def _normalize_optional_text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    return normalized


__all__ = [
    "EMAIL_MAX_LENGTH",
    "EXTERNAL_REFERENCE_MAX_LENGTH",
    "FULL_NAME_MAX_LENGTH",
    "PHONE_MAX_LENGTH",
    "SEARCH_MAX_LENGTH",
    "normalize_email",
    "normalize_external_reference",
    "normalize_full_name",
    "normalize_patient_search",
    "normalize_phone",
    "validate_date_of_birth",
    "validate_update_fields",
]
