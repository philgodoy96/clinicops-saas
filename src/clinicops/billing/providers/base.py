from typing import Protocol

from clinicops.billing.enums import BillingProvider
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


class PaymentProvider(Protocol):
    """Synchronous payment-provider boundary used by billing workflows."""

    @property
    def provider(self) -> BillingProvider:
        """Return the stable provider identifier."""

        ...

    def create_customer(
        self,
        request: CreateCustomerRequest,
    ) -> CreateCustomerResult:
        """Create or replay a provider customer mutation."""

        ...

    def create_subscription(
        self,
        request: CreateSubscriptionRequest,
    ) -> CreateSubscriptionResult:
        """Create or replay a provider subscription mutation."""

        ...

    def change_plan(
        self,
        request: ChangePlanRequest,
    ) -> ChangePlanResult:
        """Change or replay a provider subscription price mutation."""

        ...

    def cancel_subscription(
        self,
        request: CancelSubscriptionRequest,
    ) -> CancelSubscriptionResult:
        """Cancel or replay a provider subscription mutation."""

        ...
