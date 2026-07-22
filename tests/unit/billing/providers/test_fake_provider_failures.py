from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID

import pytest

from clinicops.billing.enums import ProviderOperationType
from clinicops.billing.providers.contracts import (
    ChangePlanRequest,
    CreateCustomerRequest,
    CreateSubscriptionRequest,
)
from clinicops.billing.providers.control import (
    FakeProviderControl,
    FakeProviderOutcome,
)
from clinicops.billing.providers.exceptions import (
    ProviderAmbiguousOutcomeError,
    ProviderError,
    ProviderIdempotencyConflictError,
    ProviderRetryableError,
    ProviderTerminalError,
)
from clinicops.billing.providers.fake import (
    FakePaymentProvider,
)
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
)

CREATE_CUSTOMER_KEY = build_provider_operation_key(UUID("96f33a49-1685-4ae6-aee4-fb5ebddaa94c"))
SECOND_CUSTOMER_KEY = build_provider_operation_key(UUID("82645188-3f98-4e99-91a5-e1df47682192"))
CREATE_SUBSCRIPTION_KEY = build_provider_operation_key(UUID("5fed4e42-5f15-4f7c-a20c-c79fa49f88a7"))
CHANGE_PLAN_KEY = build_provider_operation_key(UUID("6d31f2aa-0a43-4655-991a-f81777222db7"))
PERIOD_START = datetime(
    2026,
    7,
    21,
    12,
    tzinfo=UTC,
)


def _provider_with_control() -> tuple[FakePaymentProvider, FakeProviderControl]:
    control = FakeProviderControl()
    provider = FakePaymentProvider(control=control)

    return provider, control


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


def test_retryable_failure_does_not_reserve_the_operation() -> None:
    provider, control = _provider_with_control()
    request = CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)
    control.queue_outcome(
        operation_type=ProviderOperationType.CREATE_CUSTOMER,
        outcome=FakeProviderOutcome.RETRYABLE_FAILURE,
    )

    with pytest.raises(ProviderRetryableError):
        provider.create_customer(request)

    result = provider.create_customer(request)

    assert result.provider_customer_id.startswith("fake_cus_")


def test_terminal_rejection_is_replayed_for_the_same_key() -> None:
    provider, control = _provider_with_control()
    request = CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)
    control.queue_outcome(
        operation_type=ProviderOperationType.CREATE_CUSTOMER,
        outcome=FakeProviderOutcome.TERMINAL_REJECTION,
    )

    with pytest.raises(ProviderTerminalError):
        provider.create_customer(request)

    control.queue_outcome(
        operation_type=ProviderOperationType.CREATE_CUSTOMER,
        outcome=FakeProviderOutcome.SUCCESS,
    )

    with pytest.raises(ProviderTerminalError):
        provider.create_customer(request)

    different_operation = provider.create_customer(
        CreateCustomerRequest(provider_operation_key=SECOND_CUSTOMER_KEY)
    )

    assert different_operation.provider_customer_id.startswith("fake_cus_")


def test_ambiguous_customer_success_is_recovered_by_replay() -> None:
    provider, control = _provider_with_control()
    request = CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)
    control.queue_outcome(
        operation_type=ProviderOperationType.CREATE_CUSTOMER,
        outcome=FakeProviderOutcome.AMBIGUOUS_SUCCESS,
    )

    with pytest.raises(ProviderAmbiguousOutcomeError):
        provider.create_customer(request)

    recovered = provider.create_customer(request)

    assert recovered.provider_customer_id.startswith("fake_cus_")
    assert recovered.provider_reference.startswith("fake_op_")


def test_ambiguous_subscription_success_increments_version_once() -> None:
    provider, control = _provider_with_control()
    provider_customer_id = _create_customer(provider)
    request = CreateSubscriptionRequest(
        provider_operation_key=CREATE_SUBSCRIPTION_KEY,
        provider_customer_id=provider_customer_id,
        price_code="starter_monthly",
        effective_at=PERIOD_START,
    )
    control.queue_outcome(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        outcome=FakeProviderOutcome.AMBIGUOUS_SUCCESS,
    )

    with pytest.raises(ProviderAmbiguousOutcomeError):
        provider.create_subscription(request)

    recovered = provider.create_subscription(request)
    changed = provider.change_plan(
        ChangePlanRequest(
            provider_operation_key=CHANGE_PLAN_KEY,
            provider_subscription_id=(recovered.provider_subscription_id),
            target_price_code="professional_monthly",
            effective_at=recovered.current_period_end,
        )
    )

    assert recovered.provider_state_version == 1
    assert changed.provider_state_version == 2


def test_ambiguous_success_still_protects_request_identity() -> None:
    provider, control = _provider_with_control()
    provider_customer_id = _create_customer(provider)
    control.queue_outcome(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        outcome=FakeProviderOutcome.AMBIGUOUS_SUCCESS,
    )

    with pytest.raises(ProviderAmbiguousOutcomeError):
        provider.create_subscription(
            CreateSubscriptionRequest(
                provider_operation_key=(CREATE_SUBSCRIPTION_KEY),
                provider_customer_id=provider_customer_id,
                price_code="starter_monthly",
                effective_at=PERIOD_START,
            )
        )

    with pytest.raises(ProviderIdempotencyConflictError):
        provider.create_subscription(
            CreateSubscriptionRequest(
                provider_operation_key=(CREATE_SUBSCRIPTION_KEY),
                provider_customer_id=provider_customer_id,
                price_code="professional_monthly",
                effective_at=PERIOD_START,
            )
        )


def test_retryable_plan_change_can_be_retried_with_same_key() -> None:
    provider, control = _provider_with_control()
    provider_customer_id = _create_customer(provider)
    created = provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=CREATE_SUBSCRIPTION_KEY,
            provider_customer_id=provider_customer_id,
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )
    request = ChangePlanRequest(
        provider_operation_key=CHANGE_PLAN_KEY,
        provider_subscription_id=(created.provider_subscription_id),
        target_price_code="professional_monthly",
        effective_at=created.current_period_end,
    )
    control.queue_outcome(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        outcome=FakeProviderOutcome.RETRYABLE_FAILURE,
    )

    with pytest.raises(ProviderRetryableError):
        provider.change_plan(request)

    recovered = provider.change_plan(request)

    assert recovered.provider_state_version == 2
    assert recovered.effective_price_code == ("professional_monthly")


def test_ambiguous_plan_change_replays_without_second_mutation() -> None:
    provider, control = _provider_with_control()
    provider_customer_id = _create_customer(provider)
    created = provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=CREATE_SUBSCRIPTION_KEY,
            provider_customer_id=provider_customer_id,
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )
    request = ChangePlanRequest(
        provider_operation_key=CHANGE_PLAN_KEY,
        provider_subscription_id=(created.provider_subscription_id),
        target_price_code="professional_monthly",
        effective_at=created.current_period_end,
    )
    control.queue_outcome(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        outcome=FakeProviderOutcome.AMBIGUOUS_SUCCESS,
    )

    with pytest.raises(ProviderAmbiguousOutcomeError):
        provider.change_plan(request)

    recovered = provider.change_plan(request)

    assert recovered.provider_state_version == 2
    assert recovered.effective_price_code == ("professional_monthly")


def test_terminal_plan_change_rejection_is_replayed() -> None:
    provider, control = _provider_with_control()
    provider_customer_id = _create_customer(provider)
    created = provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=CREATE_SUBSCRIPTION_KEY,
            provider_customer_id=provider_customer_id,
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )
    request = ChangePlanRequest(
        provider_operation_key=CHANGE_PLAN_KEY,
        provider_subscription_id=(created.provider_subscription_id),
        target_price_code="professional_monthly",
        effective_at=created.current_period_end,
    )
    control.queue_outcome(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        outcome=FakeProviderOutcome.TERMINAL_REJECTION,
    )

    with pytest.raises(ProviderTerminalError):
        provider.change_plan(request)

    with pytest.raises(ProviderTerminalError):
        provider.change_plan(request)


def test_scripted_outcomes_are_scoped_by_operation_type() -> None:
    provider, control = _provider_with_control()
    control.queue_outcome(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        outcome=FakeProviderOutcome.RETRYABLE_FAILURE,
    )

    customer = provider.create_customer(
        CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)
    )

    assert customer.provider_customer_id.startswith("fake_cus_")


def test_ambiguous_success_is_atomic_under_concurrent_retries() -> None:
    provider, control = _provider_with_control()
    request = CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY)
    barrier = Barrier(2)
    control.queue_outcome(
        operation_type=ProviderOperationType.CREATE_CUSTOMER,
        outcome=FakeProviderOutcome.AMBIGUOUS_SUCCESS,
    )

    def call_provider() -> str:
        barrier.wait(timeout=5)

        try:
            provider.create_customer(request)
        except ProviderAmbiguousOutcomeError:
            return "ambiguous"

        return "replayed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(call_provider) for _ in range(2)]
        outcomes = [future.result(timeout=5) for future in futures]

    assert sorted(outcomes) == [
        "ambiguous",
        "replayed",
    ]


@pytest.mark.parametrize(
    (
        "outcome",
        "expected_exception",
    ),
    [
        (
            FakeProviderOutcome.RETRYABLE_FAILURE,
            ProviderRetryableError,
        ),
        (
            FakeProviderOutcome.TERMINAL_REJECTION,
            ProviderTerminalError,
        ),
        (
            FakeProviderOutcome.AMBIGUOUS_SUCCESS,
            ProviderAmbiguousOutcomeError,
        ),
    ],
)
def test_controlled_outcomes_expose_stable_exception_codes(
    outcome: FakeProviderOutcome,
    expected_exception: type[ProviderError],
) -> None:
    provider, control = _provider_with_control()
    control.queue_outcome(
        operation_type=ProviderOperationType.CREATE_CUSTOMER,
        outcome=outcome,
    )

    with pytest.raises(expected_exception) as exception_info:
        provider.create_customer(CreateCustomerRequest(provider_operation_key=CREATE_CUSTOMER_KEY))

    assert exception_info.value.code.startswith("provider_")
