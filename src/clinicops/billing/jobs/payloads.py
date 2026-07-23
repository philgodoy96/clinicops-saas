from dataclasses import dataclass
from uuid import UUID

from clinicops.jobs.contracts import JSONObject
from clinicops.jobs.runtime.exceptions import (
    InvalidJobPayloadError,
)


@dataclass(frozen=True, slots=True)
class ProcessBillingWebhookEventJobPayload:
    webhook_event_id: UUID

    @classmethod
    def from_json(
        cls,
        payload: JSONObject,
    ) -> "ProcessBillingWebhookEventJobPayload":
        if not isinstance(payload, dict):
            raise InvalidJobPayloadError("The billing webhook job payload must be a JSON object.")

        expected_fields = {"webhook_event_id"}
        received_fields = set(payload)

        if received_fields != expected_fields:
            raise InvalidJobPayloadError(
                "The billing webhook job payload must contain exactly the webhook_event_id field."
            )

        raw_webhook_event_id = payload["webhook_event_id"]

        if not isinstance(raw_webhook_event_id, str):
            raise InvalidJobPayloadError("webhook_event_id must be a canonical UUID string.")

        if raw_webhook_event_id != (raw_webhook_event_id.strip()):
            raise InvalidJobPayloadError("webhook_event_id must be a canonical UUID string.")

        try:
            webhook_event_id = UUID(raw_webhook_event_id)
        except ValueError as error:
            raise InvalidJobPayloadError(
                "webhook_event_id must be a canonical UUID string."
            ) from error

        if str(webhook_event_id) != raw_webhook_event_id.lower():
            raise InvalidJobPayloadError("webhook_event_id must be a canonical UUID string.")

        if webhook_event_id.int == 0:
            raise InvalidJobPayloadError("webhook_event_id must not be the nil UUID.")

        return cls(webhook_event_id=webhook_event_id)


__all__ = [
    "ProcessBillingWebhookEventJobPayload",
]
