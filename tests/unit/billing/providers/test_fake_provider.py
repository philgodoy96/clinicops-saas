from datetime import UTC, datetime
from uuid import UUID

import pytest

from clinicops.billing.enums import BillingProvider
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.providers.contracts import (
    CancelSubscriptionRequest,
    ChangePlanRequest,
    CreateCustomerRequest,
    CreateSubscriptionRequest,
)
from clinicops.billing.providers.exceptions import (
    ProviderInvalidStateError,
    ProviderResourceNotFoundError,
)
from clinicops.billing.providers.fake import (
    FakePaymentProvider,
)
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
)

CREATE_CUSTOMER_KEY = build_provider_operation_key(UUID("96f33a49-1685-4ae6-aee4-fb5ebddaa94c"))
CREATE_SUBSCRIPTION_KEY = build_provider_operation_key(UUID("5fed4e42-5f15-4f7c-a20c-c79fa49f88a7"))
CHANGE_PLAN_KEY = build_provider_operation_key(UUID("6d31f2aa-0a43-4655-991a-f81777222db7"))
CANCEL_SUBSCRIPTION_KEY = build_provider_operation_key(UUID("06c2d33e-d774-4a9f-b0ef-f59ebf40a3ba"))
PERIOD_START = datetime(
    2026,
    7,
    21,
    12,
    tzinfo=UTC,
)
MONTHLY_PERIOD_END = datetime(
    2026,
    8,
    21,
    12,
    tzinfo=UTC,
)


def _create_customer(
    provider: FakePaymentProvider,
) -> str:
    result = provider.create_customer(
        CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)
    )

    return result.provider_customer_id


def _create_subscription(
    provider: FakePaymentProvider,
    *,
    provider_customer_id: str,
) -> str:
    result = provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=(CREATE_SUBSCRIPTION_KEY),
            provider_customer_id=provider_customer_id,
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )

    return result.provider_subscription_id


def test_fake_provider_satisfies_payment_provider_protocol() -> None:
    provider: PaymentProvider = FakePaymentProvider()

    assert provider.provider is BillingProvider.FAKE


def test_create_customer_returns_deterministic_identifiers() -> None:
    provider = FakePaymentProvider()

    result = provider.create_customer(
        CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)
    )

    assert result.provider_customer_id == ("fake_cus_385d4e42359a0f14d91143a3")
    assert result.provider_reference == ("fake_op_385d4e42359a0f14d91143a3")


def test_create_customer_replays_the_same_result() -> None:
    provider = FakePaymentProvider()
    request = CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)

    first = provider.create_customer(request)
    second = provider.create_customer(request)

    assert second == first


def test_create_subscription_returns_confirmed_period_and_version() -> None:
    provider = FakePaymentProvider()
    provider_customer_id = _create_customer(provider)

    result = provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=(CREATE_SUBSCRIPTION_KEY),
            provider_customer_id=provider_customer_id,
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )

    assert result.provider_subscription_id.startswith("fake_sub_")
    assert result.provider_state_version == 1
    assert result.current_period_start == PERIOD_START
    assert result.current_period_end == MONTHLY_PERIOD_END
    assert result.provider_reference.startswith("fake_op_")


def test_create_subscription_replays_without_creating_again() -> None:
    provider = FakePaymentProvider()
    provider_customer_id = _create_customer(provider)
    request = CreateSubscriptionRequest(
        provider_operation_key=(CREATE_SUBSCRIPTION_KEY),
        provider_customer_id=provider_customer_id,
        price_code="starter_monthly",
        effective_at=PERIOD_START,
    )

    first = provider.create_subscription(request)
    second = provider.create_subscription(request)

    assert second == first


def test_create_subscription_requires_existing_customer() -> None:
    provider = FakePaymentProvider()

    with pytest.raises(ProviderResourceNotFoundError) as exception_info:
        provider.create_subscription(
            CreateSubscriptionRequest(
                provider_operation_key=(CREATE_SUBSCRIPTION_KEY),
                provider_customer_id="fake_cus_missing",
                price_code="starter_monthly",
                effective_at=PERIOD_START,
            )
        )

    assert exception_info.value.resource_type == "customer"


def test_create_subscription_rejects_unsupported_price() -> None:
    provider = FakePaymentProvider()
    provider_customer_id = _create_customer(provider)

    with pytest.raises(ProviderInvalidStateError) as exception_info:
        provider.create_subscription(
            CreateSubscriptionRequest(
                provider_operation_key=(CREATE_SUBSCRIPTION_KEY),
                provider_customer_id=provider_customer_id,
                price_code="unsupported_price",
                effective_at=PERIOD_START,
            )
        )

    assert exception_info.value.reason == "unsupported_price_code"


def test_provider_allows_only_one_subscription_per_customer() -> None:
    provider = FakePaymentProvider()
    provider_customer_id = _create_customer(provider)

    _create_subscription(
        provider,
        provider_customer_id=provider_customer_id,
    )

    with pytest.raises(ProviderInvalidStateError) as exception_info:
        provider.create_subscription(
            CreateSubscriptionRequest(
                provider_operation_key=(
                    build_provider_operation_key(UUID("f88e3ae5-6221-4b6f-9d8f-ef5902374af9"))
                ),
                provider_customer_id=provider_customer_id,
                price_code="professional_monthly",
                effective_at=PERIOD_START,
            )
        )

    assert exception_info.value.reason == "customer_already_has_subscription"


def test_change_plan_applies_at_period_boundary_once() -> None:
    provider = FakePaymentProvider()
    provider_customer_id = _create_customer(provider)
    provider_subscription_id = _create_subscription(
        provider,
        provider_customer_id=provider_customer_id,
    )
    request = ChangePlanRequest(
        provider_operation_key=CHANGE_PLAN_KEY,
        provider_subscription_id=(provider_subscription_id),
        target_price_code="professional_yearly",
        effective_at=MONTHLY_PERIOD_END,
    )

    first = provider.change_plan(request)
    replayed = provider.change_plan(request)

    assert first.provider_state_version == 2
    assert first.effective_price_code == ("professional_yearly")
    assert first.current_period_start == (MONTHLY_PERIOD_END)
    assert first.current_period_end == datetime(
        2027,
        8,
        21,
        12,
        tzinfo=UTC,
    )
    assert replayed == first


def test_change_plan_rejects_early_effective_at() -> None:
    provider = FakePaymentProvider()
    provider_customer_id = _create_customer(provider)
    provider_subscription_id = _create_subscription(
        provider,
        provider_customer_id=provider_customer_id,
    )

    with pytest.raises(ProviderInvalidStateError) as exception_info:
        provider.change_plan(
            ChangePlanRequest(
                provider_operation_key=CHANGE_PLAN_KEY,
                provider_subscription_id=(provider_subscription_id),
                target_price_code="professional_monthly",
                effective_at=PERIOD_START,
            )
        )

    assert exception_info.value.reason == ("billing_period_boundary_not_reached")


def test_cancel_subscription_applies_at_period_boundary_once() -> None:
    provider = FakePaymentProvider()
    provider_customer_id = _create_customer(provider)
    provider_subscription_id = _create_subscription(
        provider,
        provider_customer_id=provider_customer_id,
    )
    request = CancelSubscriptionRequest(
        provider_operation_key=(CANCEL_SUBSCRIPTION_KEY),
        provider_subscription_id=(provider_subscription_id),
        effective_at=MONTHLY_PERIOD_END,
    )

    first = provider.cancel_subscription(request)
    replayed = provider.cancel_subscription(request)

    assert first.provider_state_version == 2
    assert first.canceled_at == MONTHLY_PERIOD_END
    assert replayed == first


def test_new_mutation_is_rejected_after_cancellation() -> None:
    provider = FakePaymentProvider()
    provider_customer_id = _create_customer(provider)
    provider_subscription_id = _create_subscription(
        provider,
        provider_customer_id=provider_customer_id,
    )

    provider.cancel_subscription(
        CancelSubscriptionRequest(
            provider_operation_key=(CANCEL_SUBSCRIPTION_KEY),
            provider_subscription_id=(provider_subscription_id),
            effective_at=MONTHLY_PERIOD_END,
        )
    )

    with pytest.raises(ProviderInvalidStateError) as exception_info:
        provider.change_plan(
            ChangePlanRequest(
                provider_operation_key=CHANGE_PLAN_KEY,
                provider_subscription_id=(provider_subscription_id),
                target_price_code="professional_monthly",
                effective_at=MONTHLY_PERIOD_END,
            )
        )

    assert exception_info.value.reason == "subscription_already_canceled"
