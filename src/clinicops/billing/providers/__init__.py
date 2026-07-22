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
from clinicops.billing.providers.fake import (
    FakePaymentProvider,
)
from clinicops.billing.providers.idempotency import (
    MAX_PROVIDER_OPERATION_KEY_LENGTH,
    PROVIDER_OPERATION_KEY_PREFIX,
    build_provider_operation_key,
    fingerprint_provider_request,
    validate_provider_operation_key,
)
from clinicops.billing.providers.periods import (
    BillingPeriod,
    calculate_billing_period,
)

__all__ = [
    "MAX_PROVIDER_OPERATION_KEY_LENGTH",
    "PROVIDER_OPERATION_KEY_PREFIX",
    "BillingPeriod",
    "CancelSubscriptionRequest",
    "CancelSubscriptionResult",
    "ChangePlanRequest",
    "ChangePlanResult",
    "CreateCustomerRequest",
    "CreateCustomerResult",
    "CreateSubscriptionRequest",
    "CreateSubscriptionResult",
    "FakePaymentProvider",
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
    "calculate_billing_period",
    "fingerprint_provider_request",
    "validate_provider_operation_key",
]
