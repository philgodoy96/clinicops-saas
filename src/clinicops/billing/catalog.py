from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
)
from clinicops.billing.exceptions import UnsupportedPriceCodeError

USD_CURRENCY: Final = "USD"


@dataclass(frozen=True, slots=True)
class PriceDefinition:
    """Immutable server-owned billing price definition."""

    price_code: str
    plan: BillingPlan
    billing_interval: BillingInterval
    currency: str
    unit_amount: int
    provider_price_code: str


_PRICE_DEFINITIONS: Final[tuple[PriceDefinition, ...]] = (
    PriceDefinition(
        price_code="starter_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency=USD_CURRENCY,
        unit_amount=4_900,
        provider_price_code="fake_price_starter_monthly_v1",
    ),
    PriceDefinition(
        price_code="starter_yearly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.YEARLY,
        currency=USD_CURRENCY,
        unit_amount=49_000,
        provider_price_code="fake_price_starter_yearly_v1",
    ),
    PriceDefinition(
        price_code="professional_monthly",
        plan=BillingPlan.PROFESSIONAL,
        billing_interval=BillingInterval.MONTHLY,
        currency=USD_CURRENCY,
        unit_amount=9_900,
        provider_price_code="fake_price_professional_monthly_v1",
    ),
    PriceDefinition(
        price_code="professional_yearly",
        plan=BillingPlan.PROFESSIONAL,
        billing_interval=BillingInterval.YEARLY,
        currency=USD_CURRENCY,
        unit_amount=99_000,
        provider_price_code="fake_price_professional_yearly_v1",
    ),
)

_PRICE_DEFINITIONS_BY_CODE: Final[Mapping[str, PriceDefinition]] = {
    definition.price_code: definition for definition in _PRICE_DEFINITIONS
}

PRICE_CODES: Final[frozenset[str]] = frozenset(_PRICE_DEFINITIONS_BY_CODE)


def get_price_definition(price_code: str) -> PriceDefinition:
    """Return one immutable price definition by its stable public code."""

    normalized_price_code = price_code.strip()

    try:
        return _PRICE_DEFINITIONS_BY_CODE[normalized_price_code]
    except KeyError as error:
        raise UnsupportedPriceCodeError(normalized_price_code) from error


def list_price_definitions() -> tuple[PriceDefinition, ...]:
    """Return all supported prices in stable catalog order."""

    return _PRICE_DEFINITIONS
