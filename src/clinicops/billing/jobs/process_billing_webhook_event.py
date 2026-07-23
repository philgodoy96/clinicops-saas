from collections.abc import Callable
from uuid import UUID

from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
    BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION,
)
from clinicops.billing.jobs.payloads import (
    ProcessBillingWebhookEventJobPayload,
)
from clinicops.jobs.contracts import (
    ClaimedBackgroundJob,
)
from clinicops.jobs.runtime.exceptions import (
    TerminalJobExecutionError,
    UnsupportedJobPayloadVersionError,
)

type ProcessBillingWebhookEvent = Callable[
    [UUID],
    None,
]


class ProcessBillingWebhookEventJobHandler:
    def __init__(
        self,
        process_webhook_event: (ProcessBillingWebhookEvent),
    ) -> None:
        if not callable(process_webhook_event):
            raise TypeError("process_webhook_event must be callable.")

        self._process_webhook_event = process_webhook_event

    @property
    def job_type(self) -> str:
        return BILLING_WEBHOOK_PROCESS_JOB_TYPE

    @property
    def supported_payload_version(self) -> int:
        return BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION

    def execute(
        self,
        job: ClaimedBackgroundJob,
    ) -> None:
        if job.job_type != self.job_type:
            raise TerminalJobExecutionError(
                "The billing webhook handler received an unexpected job type.",
                error_code="unexpected_billing_job_type",
            )

        if job.payload_version != self.supported_payload_version:
            raise UnsupportedJobPayloadVersionError(
                job_type=job.job_type,
                received_version=job.payload_version,
                supported_version=(self.supported_payload_version),
            )

        payload = ProcessBillingWebhookEventJobPayload.from_json(job.payload)

        self._process_webhook_event(payload.webhook_event_id)


__all__ = [
    "ProcessBillingWebhookEvent",
    "ProcessBillingWebhookEventJobHandler",
]
