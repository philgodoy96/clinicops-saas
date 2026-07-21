from clinicops.billing.enums import SubscriptionStatus
from clinicops.core.exceptions import ApplicationError


class BillingError(ApplicationError):
    """Base error for billing application and domain failures."""

    code = "billing_error"
    public_message = "The billing operation could not be completed."


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
