from enum import StrEnum


class BillingPlan(StrEnum):
    """Supported ClinicOps subscription tiers."""

    STARTER = "starter"
    PROFESSIONAL = "professional"


class BillingInterval(StrEnum):
    """Supported subscription billing intervals."""

    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionStatus(StrEnum):
    """Confirmed local subscription lifecycle states."""

    PENDING = "pending"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class BillingProvider(StrEnum):
    """Supported payment-provider identifiers."""

    FAKE = "fake"


class ProviderOperationType(StrEnum):
    """Supported outbound payment-provider operation types."""

    CREATE_CUSTOMER = "create_customer"
    CREATE_SUBSCRIPTION = "create_subscription"
    CHANGE_PLAN = "change_plan"
    CANCEL_SUBSCRIPTION = "cancel_subscription"
