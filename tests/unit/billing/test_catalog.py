from dataclasses import FrozenInstanceError

import pytest

from clinicops.billing.catalog import (
    PRICE_CODES,
    USD_CURRENCY,
    PriceDefinition,
    get_price_definition,
    list_price_definitions,
)
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import UnsupportedPriceCodeError


def test_billing_enums_expose_stable_public_values() -> None:
    assert BillingPlan.STARTER.value == "starter"
    assert BillingPlan.PROFESSIONAL.value == "professional"

    assert BillingInterval.MONTHLY.value == "monthly"
    assert BillingInterval.YEARLY.value == "yearly"

    assert SubscriptionStatus.PENDING.value == "pending"
    assert SubscriptionStatus.ACTIVE.value == "active"
    assert SubscriptionStatus.PAST_DUE.value == "past_due"
    assert SubscriptionStatus.CANCELED.value == "canceled"

    assert BillingProvider.FAKE.value == "fake"


def test_catalog_contains_the_supported_v1_prices() -> None:
    assert (
        frozenset(
            {
                "starter_monthly",
                "starter_yearly",
                "professional_monthly",
                "professional_yearly",
            }
        )
        == PRICE_CODES
    )


@pytest.mark.parametrize(
    (
        "price_code",
        "expected_plan",
        "expected_interval",
        "expected_amount",
        "expected_provider_price_code",
    ),
    [
        (
            "starter_monthly",
            BillingPlan.STARTER,
            BillingInterval.MONTHLY,
            4_900,
            "fake_price_starter_monthly_v1",
        ),
        (
            "starter_yearly",
            BillingPlan.STARTER,
            BillingInterval.YEARLY,
            49_000,
            "fake_price_starter_yearly_v1",
        ),
        (
            "professional_monthly",
            BillingPlan.PROFESSIONAL,
            BillingInterval.MONTHLY,
            9_900,
            "fake_price_professional_monthly_v1",
        ),
        (
            "professional_yearly",
            BillingPlan.PROFESSIONAL,
            BillingInterval.YEARLY,
            99_000,
            "fake_price_professional_yearly_v1",
        ),
    ],
)
def test_catalog_resolves_price_definitions(
    price_code: str,
    expected_plan: BillingPlan,
    expected_interval: BillingInterval,
    expected_amount: int,
    expected_provider_price_code: str,
) -> None:
    definition = get_price_definition(price_code)

    assert definition.price_code == price_code
    assert definition.plan is expected_plan
    assert definition.billing_interval is expected_interval
    assert definition.currency == USD_CURRENCY
    assert definition.unit_amount == expected_amount
    assert definition.provider_price_code == expected_provider_price_code


def test_catalog_lookup_strips_external_whitespace() -> None:
    definition = get_price_definition("  starter_monthly  ")

    assert definition.price_code == "starter_monthly"


def test_catalog_lookup_remains_case_sensitive() -> None:
    with pytest.raises(UnsupportedPriceCodeError):
        get_price_definition("STARTER_MONTHLY")


def test_catalog_rejects_an_unknown_price_code() -> None:
    with pytest.raises(UnsupportedPriceCodeError) as exception_info:
        get_price_definition("enterprise_monthly")

    error = exception_info.value

    assert error.price_code == "enterprise_monthly"
    assert error.code == "unsupported_price_code"
    assert error.public_message == "The selected billing price is not supported."


def test_catalog_returns_an_immutable_stable_sequence() -> None:
    definitions = list_price_definitions()

    assert isinstance(definitions, tuple)
    assert [definition.price_code for definition in definitions] == [
        "starter_monthly",
        "starter_yearly",
        "professional_monthly",
        "professional_yearly",
    ]


def test_price_definitions_are_immutable() -> None:
    definition = get_price_definition("starter_monthly")

    with pytest.raises(FrozenInstanceError):
        definition.unit_amount = 0  # type: ignore[misc]


def test_catalog_amounts_are_positive_minor_units() -> None:
    for definition in list_price_definitions():
        assert isinstance(definition.unit_amount, int)
        assert definition.unit_amount > 0


def test_price_definition_contract_uses_domain_enums() -> None:
    definition = PriceDefinition(
        price_code="test_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=1_000,
        provider_price_code="fake_price_test_monthly_v1",
    )

    assert definition.plan is BillingPlan.STARTER
    assert definition.billing_interval is BillingInterval.MONTHLY
