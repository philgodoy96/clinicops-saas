from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from clinicops.billing.providers.contracts import (
    CancelSubscriptionRequest,
    CancelSubscriptionResult,
    ChangePlanRequest,
    ChangePlanResult,
    CreateCustomerRequest,
    CreateCustomerResult,
    CreateSubscriptionRequest,
    CreateSubscriptionResult,
)
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
)

PROVIDER_OPERATION_KEY = build_provider_operation_key(uuid4())
PERIOD_START = datetime(
    2026,
    7,
    21,
    12,
    tzinfo=UTC,
)
PERIOD_END = datetime(
    2026,
    8,
    21,
    12,
    tzinfo=UTC,
)


def test_create_customer_contracts_are_immutable() -> None:
    request = CreateCustomerRequest(provider_operation_key=PROVIDER_OPERATION_KEY)
    result = CreateCustomerResult(
        provider_customer_id="fake_cus_123",
        provider_reference="fake_op_123",
    )

    with pytest.raises(FrozenInstanceError):
        request.provider_operation_key = "another-key"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        result.provider_customer_id = "another-id"  # type: ignore[misc]


def test_create_subscription_request_normalizes_datetime_to_utc() -> None:
    effective_at = datetime(
        2026,
        7,
        21,
        9,
        tzinfo=timezone(-timedelta(hours=3)),
    )

    request = CreateSubscriptionRequest(
        provider_operation_key=PROVIDER_OPERATION_KEY,
        provider_customer_id=" fake_cus_123 ",
        price_code=" starter_monthly ",
        effective_at=effective_at,
    )

    assert request.provider_customer_id == "fake_cus_123"
    assert request.price_code == "starter_monthly"
    assert request.effective_at == PERIOD_START
    assert request.effective_at.tzinfo is UTC


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: CreateSubscriptionRequest(
            provider_operation_key=PROVIDER_OPERATION_KEY,
            provider_customer_id="fake_cus_123",
            price_code="starter_monthly",
            effective_at=datetime(2026, 7, 21),
        ),
        lambda: ChangePlanRequest(
            provider_operation_key=PROVIDER_OPERATION_KEY,
            provider_subscription_id="fake_sub_123",
            target_price_code="professional_monthly",
            effective_at=datetime(2026, 8, 21),
        ),
        lambda: CancelSubscriptionRequest(
            provider_operation_key=PROVIDER_OPERATION_KEY,
            provider_subscription_id="fake_sub_123",
            effective_at=datetime(2026, 8, 21),
        ),
    ],
)
def test_provider_requests_reject_naive_datetimes(
    request_factory: Callable[[], object],
) -> None:
    with pytest.raises(
        ValueError,
        match="must be timezone-aware",
    ):
        request_factory()


def test_create_subscription_result_requires_an_ordered_period() -> None:
    with pytest.raises(
        ValueError,
        match="must be earlier",
    ):
        CreateSubscriptionResult(
            provider_subscription_id="fake_sub_123",
            provider_state_version=1,
            current_period_start=PERIOD_END,
            current_period_end=PERIOD_START,
            provider_reference="fake_op_123",
        )


@pytest.mark.parametrize(
    "result_factory",
    [
        lambda: CreateSubscriptionResult(
            provider_subscription_id="fake_sub_123",
            provider_state_version=-1,
            current_period_start=PERIOD_START,
            current_period_end=PERIOD_END,
            provider_reference="fake_op_123",
        ),
        lambda: ChangePlanResult(
            provider_subscription_id="fake_sub_123",
            provider_state_version=-1,
            effective_price_code="professional_monthly",
            current_period_start=PERIOD_START,
            current_period_end=PERIOD_END,
            provider_reference="fake_op_123",
        ),
        lambda: CancelSubscriptionResult(
            provider_subscription_id="fake_sub_123",
            provider_state_version=-1,
            canceled_at=PERIOD_END,
            provider_reference="fake_op_123",
        ),
    ],
)
def test_provider_results_reject_negative_state_versions(
    result_factory: Callable[[], object],
) -> None:
    with pytest.raises(
        ValueError,
        match="must not be negative",
    ):
        result_factory()


@pytest.mark.parametrize(
    "contract_factory",
    [
        lambda: CreateCustomerResult(
            provider_customer_id=" ",
            provider_reference="fake_op_123",
        ),
        lambda: CreateSubscriptionRequest(
            provider_operation_key=PROVIDER_OPERATION_KEY,
            provider_customer_id=" ",
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        ),
        lambda: ChangePlanRequest(
            provider_operation_key=PROVIDER_OPERATION_KEY,
            provider_subscription_id="fake_sub_123",
            target_price_code=" ",
            effective_at=PERIOD_END,
        ),
    ],
)
def test_provider_contracts_reject_empty_identifiers(
    contract_factory: Callable[[], object],
) -> None:
    with pytest.raises(
        ValueError,
        match="must not be empty",
    ):
        contract_factory()
