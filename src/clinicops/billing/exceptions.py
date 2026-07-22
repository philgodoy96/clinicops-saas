from enum import StrEnum

from clinicops.billing.enums import SubscriptionStatus
from clinicops.core.exceptions import ApplicationError


class BillingError(ApplicationError):
    """Base error for billing application and domain failures."""

    code = "billing_error"
    public_message = "The billing operation could not be completed."


class IdempotencyKeyViolation(StrEnum):
    """Machine-readable idempotency-key validation failures."""

    EMPTY = "empty"
    TOO_LONG = "too_long"


class InvalidIdempotencyKeyError(BillingError):
    """Raised when a client idempotency key is invalid."""

    code = "invalid_idempotency_key"
    public_message = "The billing idempotency key is invalid."

    violation: IdempotencyKeyViolation

    def __init__(
        self,
        violation: IdempotencyKeyViolation,
    ) -> None:
        self.violation = violation
        super().__init__(f"Invalid billing idempotency key: {violation.value}.")


class UnsupportedPriceCodeError(BillingError):
    """Raised when a price code does not exist in the billing catalog."""

    code = "unsupported_price_code"
    public_message = "The selected billing price is not supported."

    price_code: str

    def __init__(self, price_code: str) -> None:
        self.price_code = price_code
        super().__init__(f"Unsupported billing price code: {price_code!r}.")


class InvalidSubscriptionTransitionError(BillingError):
    """Raised when a subscription status transition is not permitted."""

    code = "invalid_subscription_transition"
    public_message = "The requested subscription status transition is not permitted."

    current_status: SubscriptionStatus
    target_status: SubscriptionStatus

    def __init__(
        self,
        current_status: SubscriptionStatus,
        target_status: SubscriptionStatus,
    ) -> None:
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            "Invalid subscription transition from "
            f"{current_status.value!r} to {target_status.value!r}."
        )


class SubscriptionMutationNotAllowedError(BillingError):
    """Raised when a mutation is unavailable in the current status."""

    code = "subscription_mutation_not_allowed"
    public_message = "The subscription cannot be modified in its current state."

    status: SubscriptionStatus
    operation: str

    def __init__(
        self,
        status: SubscriptionStatus,
        operation: str,
    ) -> None:
        self.status = status
        self.operation = operation
        super().__init__(
            f"Operation {operation!r} is not permitted while the "
            f"subscription status is {status.value!r}."
        )


class PlanAlreadySelectedError(BillingError):
    """Raised when the requested price is already active."""

    code = "plan_already_selected"
    public_message = "The selected billing price is already active."

    price_code: str

    def __init__(self, price_code: str) -> None:
        self.price_code = price_code
        super().__init__(f"Billing price {price_code!r} is already active.")


class PendingPlanChangeError(BillingError):
    """Raised when another price change is already pending."""

    code = "pending_plan_change"
    public_message = "A billing price change is already pending."

    pending_price_code: str

    def __init__(self, pending_price_code: str) -> None:
        self.pending_price_code = pending_price_code
        super().__init__(f"A billing price change is already pending for {pending_price_code!r}.")


class PendingPlanChangeNotFoundError(BillingError):
    """Raised when no scheduled price change can be confirmed."""

    code = "pending_plan_change_not_found"
    public_message = "No billing price change is currently pending."


class CancellationAlreadyRequestedError(BillingError):
    """Raised when period-end cancellation is already scheduled."""

    code = "cancellation_already_requested"
    public_message = "Subscription cancellation is already scheduled."


class CancellationNotRequestedError(BillingError):
    """Raised when cancellation confirmation has no prior request."""

    code = "cancellation_not_requested"
    public_message = "Subscription cancellation has not been scheduled."


class CanceledSubscriptionMutationError(BillingError):
    """Raised when a canceled subscription is modified."""

    code = "canceled_subscription_mutation"
    public_message = "A canceled subscription cannot be modified."


class BillingPeriodBoundaryNotReachedError(BillingError):
    """Raised when a scheduled transition is confirmed too early."""

    code = "billing_period_boundary_not_reached"
    public_message = "The current billing period has not ended yet."


class BillingCustomerAlreadyExistsError(BillingError):
    """Raised when a tenant already has a customer for the provider."""

    code = "billing_customer_already_exists"
    public_message = "A billing customer already exists for this tenant and provider."


class BillingProviderCustomerAlreadyLinkedError(BillingError):
    """Raised when a provider customer identifier is already linked."""

    code = "billing_provider_customer_already_linked"
    public_message = "The provider customer is already linked to another tenant."


class BillingSubscriptionAlreadyExistsError(BillingError):
    """Raised when a tenant already has its V1 subscription lifecycle."""

    code = "billing_subscription_already_exists"
    public_message = "A billing subscription already exists for this tenant."


class BillingProviderSubscriptionAlreadyLinkedError(BillingError):
    """Raised when a provider subscription identifier is already linked."""

    code = "billing_provider_subscription_already_linked"
    public_message = "The provider subscription is already linked to another tenant."


class ProviderOperationAlreadyExistsError(BillingError):
    """Raised when a provider operation idempotency scope already exists."""

    code = "provider_operation_already_exists"
    public_message = "The billing provider operation already exists."


class BillingWebhookEventAlreadyExistsError(BillingError):
    """Raised when the provider event has already been persisted."""

    code = "billing_webhook_event_already_exists"
    public_message = "The billing webhook event has already been received."


class MissingIdempotencyKeyError(BillingError):
    """Raised when a billing mutation omits its client idempotency key."""

    code = "missing_idempotency_key"
    public_message = "The Idempotency-Key header is required."

    def __init__(self) -> None:
        super().__init__("The billing mutation did not include an Idempotency-Key header.")


class BillingIdempotencyConflictError(BillingError):
    """Raised when one client key is reused for another command."""

    code = "billing_idempotency_conflict"
    public_message = "The idempotency key was reused for a different billing request."

    def __init__(self) -> None:
        super().__init__(
            "The persisted billing command fingerprint does not match the current request."
        )


class ProviderOperationInProgressError(BillingError):
    """Raised when another request owns provider execution."""

    code = "provider_operation_in_progress"
    public_message = "The billing operation is already in progress."

    def __init__(self) -> None:
        super().__init__(
            "A provider operation with the same tenant, type, "
            "and idempotency key is already in progress."
        )


class BillingSubscriptionNotFoundError(BillingError):
    """Raised when a tenant has no persisted billing subscription."""

    code = "billing_subscription_not_found"
    public_message = "The billing subscription was not found."

    def __init__(self) -> None:
        super().__init__("No billing subscription exists for the authorized tenant.")
