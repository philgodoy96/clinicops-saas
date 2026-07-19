import pytest

from clinicops.identity.email import canonicalize_email
from clinicops.identity.exceptions import InvalidEmailError


def test_email_is_trimmed_validated_and_lowercased() -> None:
    assert canonicalize_email("  USER.Name+Clinic@EXAMPLE.COM  ") == "user.name+clinic@example.com"


def test_email_validation_does_not_require_dns_deliverability() -> None:
    email = "user@domain-that-does-not-exist-12345.com"

    assert canonicalize_email(email) == email


@pytest.mark.parametrize(
    "raw_email",
    [
        "",
        "not-an-email",
        "user@localhost",
        "Display Name <user@example.com>",
    ],
)
def test_invalid_email_is_rejected(raw_email: str) -> None:
    with pytest.raises(InvalidEmailError):
        canonicalize_email(raw_email)
