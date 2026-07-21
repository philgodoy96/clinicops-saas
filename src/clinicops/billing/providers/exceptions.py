from clinicops.billing.enums import (
    BillingProvider,
    ProviderOperationType,
)
from clinicops.core.exceptions import ApplicationError


class InvalidProviderOperationKeyError(ApplicationError):
    """Raised when an internal provider operation key is invalid."""

    code = "invalid_provider_operation_key"
    public_message = "The provider operation key is invalid."

    def __init__(self) -> None:
        super().__init__("The provider operation key does not match the required ClinicOps format.")


class ProviderError(ApplicationError):
    """Base error raised by a payment-provider boundary."""

    provider: BillingProvider
    operation_type: ProviderOperationType
    retryable: bool = False

    def __init__(
        self,
        *,
        provider: BillingProvider,
        operation_type: ProviderOperationType,
        internal_message: str,
    ) -> None:
        self.provider = provider
        self.operation_type = operation_type
        super().__init__(internal_message)


class ProviderRetryableError(ProviderError):
    """Raised when a provider operation may be retried safely."""

    code = "provider_retryable_failure"
    public_message = "The payment provider operation failed temporarily."
    retryable = True


class ProviderTerminalError(ProviderError):
    """Raised when retrying the provider operation cannot succeed."""

    code = "provider_terminal_failure"
    public_message = "The payment provider operation was rejected."
    retryable = False


class ProviderAmbiguousOutcomeError(ProviderRetryableError):
    """Raised when provider-side success may have occurred."""

    code = "provider_ambiguous_outcome"
    public_message = "The payment provider outcome could not be confirmed."


class ProviderIdempotencyConflictError(ProviderTerminalError):
    """Raised when one provider key is reused for another request."""

    code = "provider_idempotency_conflict"
    public_message = "The provider operation key was reused for a different request."

    provider_operation_key: str

    def __init__(
        self,
        *,
        provider: BillingProvider,
        operation_type: ProviderOperationType,
        provider_operation_key: str,
    ) -> None:
        self.provider_operation_key = provider_operation_key
        super().__init__(
            provider=provider,
            operation_type=operation_type,
            internal_message=(
                "Provider operation key reuse produced a request fingerprint mismatch."
            ),
        )


class ProviderResourceNotFoundError(ProviderTerminalError):
    """Raised when a referenced provider resource does not exist."""

    code = "provider_resource_not_found"
    public_message = "The requested payment provider resource was not found."

    resource_type: str
    resource_id: str

    def __init__(
        self,
        *,
        provider: BillingProvider,
        operation_type: ProviderOperationType,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(
            provider=provider,
            operation_type=operation_type,
            internal_message=(f"Provider {resource_type} was not found."),
        )


class ProviderInvalidStateError(ProviderTerminalError):
    """Raised when a provider resource cannot accept a mutation."""

    code = "provider_invalid_state"
    public_message = "The payment provider resource cannot accept this operation."

    reason: str

    def __init__(
        self,
        *,
        provider: BillingProvider,
        operation_type: ProviderOperationType,
        reason: str,
    ) -> None:
        self.reason = reason
        super().__init__(
            provider=provider,
            operation_type=operation_type,
            internal_message=(
                f"Provider resource rejected the requested state transition: {reason}."
            ),
        )
