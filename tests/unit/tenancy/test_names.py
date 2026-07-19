import pytest

from clinicops.tenancy.exceptions import (
    InvalidTenantNameError,
    TenantNameViolation,
)
from clinicops.tenancy.names import (
    MAX_TENANT_NAME_LENGTH,
    MIN_TENANT_NAME_LENGTH,
    normalize_tenant_name,
)


def test_tenant_name_is_trimmed_without_collapsing_internal_space() -> None:
    assert normalize_tenant_name("  São   Lucas Clinic  ") == "São   Lucas Clinic"


def test_tenant_name_accepts_boundary_lengths() -> None:
    assert normalize_tenant_name("a" * MIN_TENANT_NAME_LENGTH) == "a" * MIN_TENANT_NAME_LENGTH
    assert normalize_tenant_name("a" * MAX_TENANT_NAME_LENGTH) == "a" * MAX_TENANT_NAME_LENGTH


@pytest.mark.parametrize(
    "raw_name",
    [
        "",
        " ",
        "a",
        " a ",
    ],
)
def test_tenant_name_rejects_short_values(raw_name: str) -> None:
    with pytest.raises(InvalidTenantNameError) as exception_info:
        normalize_tenant_name(raw_name)

    assert exception_info.value.violation is TenantNameViolation.TOO_SHORT


def test_tenant_name_rejects_long_values() -> None:
    with pytest.raises(InvalidTenantNameError) as exception_info:
        normalize_tenant_name("a" * (MAX_TENANT_NAME_LENGTH + 1))

    assert exception_info.value.violation is TenantNameViolation.TOO_LONG
