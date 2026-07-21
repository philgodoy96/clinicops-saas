import pytest

from clinicops.billing.enums import (
    BillingProvider,
    ProviderOperationType,
)
from clinicops.billing.providers.exceptions import (
    InvalidProviderOperationKeyError,
    ProviderAmbiguousOutcomeError,
    ProviderIdempotencyConflictError,
    ProviderInvalidStateError,
    ProviderResourceNotFoundError,
    ProviderRetryableError,
    ProviderTerminalError,
)


@pytest.mark.parametrize(
    (
        "error",
        "expected_code",
        "expected_retryable",
    ),
    [
        (
            ProviderRetryableError(
                provider=BillingProvider.FAKE,
                operation_type=(ProviderOperationType.CREATE_CUSTOMER),
                internal_message="Temporary provider failure.",
            ),
            "provider_retryable_failure",
            True,
        ),
        (
            ProviderTerminalError(
                provider=BillingProvider.FAKE,
                operation_type=(ProviderOperationType.CREATE_CUSTOMER),
                internal_message="Provider rejected the request.",
            ),
            "provider_terminal_failure",
            False,
        ),
        (
            ProviderAmbiguousOutcomeError(
                provider=BillingProvider.FAKE,
                operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
                internal_message=("Provider outcome could not be confirmed."),
            ),
            "provider_ambiguous_outcome",
            True,
        ),
    ],
)
def test_provider_failures_have_stable_contracts(
    error: ProviderRetryableError | ProviderTerminalError,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    assert error.code == expected_code
    assert error.retryable is expected_retryable
    assert error.provider is BillingProvider.FAKE


def test_provider_idempotency_conflict_keeps_operation_context() -> None:
    error = ProviderIdempotencyConflictError(
        provider=BillingProvider.FAKE,
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        provider_operation_key=("clinicops:96f33a49-1685-4ae6-aee4-fb5ebddaa94c"),
    )

    assert error.code == "provider_idempotency_conflict"
    assert error.retryable is False
    assert error.operation_type is ProviderOperationType.CREATE_SUBSCRIPTION
    assert error.provider_operation_key.startswith("clinicops:")


def test_provider_resource_not_found_keeps_resource_context() -> None:
    error = ProviderResourceNotFoundError(
        provider=BillingProvider.FAKE,
        operation_type=ProviderOperationType.CHANGE_PLAN,
        resource_type="subscription",
        resource_id="fake_sub_123",
    )

    assert error.code == "provider_resource_not_found"
    assert error.resource_type == "subscription"
    assert error.resource_id == "fake_sub_123"


def test_provider_invalid_state_keeps_reason() -> None:
    error = ProviderInvalidStateError(
        provider=BillingProvider.FAKE,
        operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
        reason="subscription_already_canceled",
    )

    assert error.code == "provider_invalid_state"
    assert error.reason == "subscription_already_canceled"
    assert error.retryable is False


def test_invalid_provider_operation_key_has_stable_contract() -> None:
    error = InvalidProviderOperationKeyError()

    assert error.code == "invalid_provider_operation_key"
    assert error.public_message == "The provider operation key is invalid."
