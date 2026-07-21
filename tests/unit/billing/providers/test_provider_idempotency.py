from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from clinicops.billing.enums import (
    BillingInterval,
    ProviderOperationType,
)
from clinicops.billing.providers.exceptions import (
    InvalidProviderOperationKeyError,
)
from clinicops.billing.providers.idempotency import (
    PROVIDER_OPERATION_KEY_PREFIX,
    build_provider_operation_key,
    fingerprint_provider_request,
    validate_provider_operation_key,
)


def test_provider_operation_key_uses_the_persisted_operation_id() -> None:
    provider_operation_id = UUID("96f33a49-1685-4ae6-aee4-fb5ebddaa94c")

    key = build_provider_operation_key(provider_operation_id)

    assert key == ("clinicops:96f33a49-1685-4ae6-aee4-fb5ebddaa94c")
    assert validate_provider_operation_key(key) == key


def test_provider_operation_key_strips_external_whitespace() -> None:
    key = build_provider_operation_key(uuid4())

    assert validate_provider_operation_key(f"  {key}  ") == key


@pytest.mark.parametrize(
    "raw_key",
    [
        "",
        " ",
        "wrong-prefix:96f33a49-1685-4ae6-aee4-fb5ebddaa94c",
        "clinicops:not-a-uuid",
        "clinicops:96F33A49-1685-4AE6-AEE4-FB5EBDDAA94C",
        ("clinicops:96f33a4916854ae6aee4fb5ebddaa94c"),
    ],
)
def test_provider_operation_key_rejects_noncanonical_values(
    raw_key: str,
) -> None:
    with pytest.raises(InvalidProviderOperationKeyError):
        validate_provider_operation_key(raw_key)


def test_provider_request_fingerprint_matches_canonical_contract() -> None:
    fingerprint = fingerprint_provider_request(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        fields={
            "provider_customer_id": "fake_cus_123",
            "price_code": "starter_monthly",
            "effective_at": datetime(
                2026,
                7,
                21,
                12,
                tzinfo=UTC,
            ),
        },
    )

    assert fingerprint == ("407ac68eeef3f72522e1f1e70fe29dc8b7b504e4a2d710355423c409d57dedf6")


def test_provider_request_fingerprint_is_order_independent() -> None:
    effective_at = datetime(
        2026,
        7,
        21,
        12,
        tzinfo=UTC,
    )

    first = fingerprint_provider_request(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        fields={
            "provider_subscription_id": "fake_sub_123",
            "target_price_code": "professional_yearly",
            "effective_at": effective_at,
        },
    )
    second = fingerprint_provider_request(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        fields={
            "effective_at": effective_at,
            "target_price_code": "professional_yearly",
            "provider_subscription_id": "fake_sub_123",
        },
    )

    assert first == second


def test_provider_request_fingerprint_normalizes_supported_values() -> None:
    utc_timestamp = datetime(
        2026,
        7,
        21,
        12,
        tzinfo=UTC,
    )
    offset_timestamp = datetime(
        2026,
        7,
        21,
        9,
        tzinfo=timezone(-timedelta(hours=3)),
    )
    provider_customer_id = uuid4()

    first = fingerprint_provider_request(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        fields={
            "resource_id": provider_customer_id,
            "billing_interval": BillingInterval.MONTHLY,
            "effective_at": utc_timestamp,
        },
    )
    second = fingerprint_provider_request(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        fields={
            "effective_at": offset_timestamp,
            "billing_interval": "monthly",
            "resource_id": str(provider_customer_id),
        },
    )

    assert first == second


def test_provider_request_fingerprint_rejects_naive_datetime() -> None:
    with pytest.raises(
        ValueError,
        match="must be timezone-aware",
    ):
        fingerprint_provider_request(
            operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
            fields={
                "effective_at": datetime(2026, 7, 21),
            },
        )


def test_provider_operation_key_prefix_is_stable() -> None:
    assert PROVIDER_OPERATION_KEY_PREFIX == "clinicops:"
