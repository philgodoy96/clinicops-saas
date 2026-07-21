"""Payment-provider contracts and implementations for billing."""

from clinicops.billing.providers.base import PaymentProvider
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
from clinicops.billing.providers.exceptions import (
    InvalidProviderOperationKeyError,
    ProviderAmbiguousOutcomeError,
    ProviderError,
    ProviderIdempotencyConflictError,
    ProviderInvalidStateError,
    ProviderResourceNotFoundError,
    ProviderRetryableError,
    ProviderTerminalError,
)
from clinicops.billing.providers.idempotency import (
    MAX_PROVIDER_OPERATION_KEY_LENGTH,
    PROVIDER_OPERATION_KEY_PREFIX,
    build_provider_operation_key,
    fingerprint_provider_request,
    validate_provider_operation_key,
)

__all__ = [
    "MAX_PROVIDER_OPERATION_KEY_LENGTH",
    "PROVIDER_OPERATION_KEY_PREFIX",
    "CancelSubscriptionRequest",
    "CancelSubscriptionResult",
    "ChangePlanRequest",
    "ChangePlanResult",
    "CreateCustomerRequest",
    "CreateCustomerResult",
    "CreateSubscriptionRequest",
    "CreateSubscriptionResult",
    "InvalidProviderOperationKeyError",
    "PaymentProvider",
    "ProviderAmbiguousOutcomeError",
    "ProviderError",
    "ProviderIdempotencyConflictError",
    "ProviderInvalidStateError",
    "ProviderResourceNotFoundError",
    "ProviderRetryableError",
    "ProviderTerminalError",
    "build_provider_operation_key",
    "fingerprint_provider_request",
    "validate_provider_operation_key",
]
