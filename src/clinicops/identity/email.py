from email_validator import EmailNotValidError, validate_email

from clinicops.identity.exceptions import InvalidEmailError


def canonicalize_email(raw_email: str) -> str:
    """Validate and return the canonical global identity email."""

    candidate = raw_email.strip()

    try:
        validated_email = validate_email(
            candidate,
            check_deliverability=False,
        )
    except EmailNotValidError as exc:
        raise InvalidEmailError(str(exc)) from exc

    return validated_email.normalized.lower()
