from dataclasses import dataclass
from uuid import UUID

from clinicops.billing.enums import (
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    BillingWebhookProcessingOutcome,
)


@dataclass(frozen=True, slots=True)
class ProcessBillingWebhookEventCommand:
    """Identify one durably ingested billing event to process."""

    webhook_event_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.webhook_event_id, UUID):
            raise TypeError("The billing webhook event ID must be a UUID.")


@dataclass(frozen=True, slots=True)
class ProcessedBillingWebhookEvent:
    """Public application result for a completed processing attempt."""

    webhook_event_id: UUID
    provider_event_id: str
    event_type: BillingWebhookEventType
    status: BillingWebhookEventStatus
    outcome: BillingWebhookProcessingOutcome
    subscription_id: UUID
    processing_attempt_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.webhook_event_id, UUID):
            raise TypeError("The billing webhook event ID must be a UUID.")

        if not isinstance(self.subscription_id, UUID):
            raise TypeError("The processed billing subscription ID must be a UUID.")

        normalized_provider_event_id = self.provider_event_id.strip()

        if not normalized_provider_event_id:
            raise ValueError("The provider event ID must not be empty.")

        object.__setattr__(
            self,
            "provider_event_id",
            normalized_provider_event_id,
        )

        if self.processing_attempt_count < 1:
            raise ValueError(
                "A completed billing webhook processing result must include at least one attempt."
            )

        expected_status = {
            BillingWebhookProcessingOutcome.APPLIED: (BillingWebhookEventStatus.PROCESSED),
            BillingWebhookProcessingOutcome.IGNORED: (BillingWebhookEventStatus.IGNORED),
        }[self.outcome]

        if self.status is not expected_status:
            raise ValueError(
                "The billing webhook processing outcome does not match the persisted event status."
            )
