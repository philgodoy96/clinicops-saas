from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
    BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION,
)
from clinicops.billing.jobs.payloads import (
    ProcessBillingWebhookEventJobPayload,
)
from clinicops.jobs.contracts import JSONObject
from clinicops.jobs.runtime.exceptions import (
    InvalidJobPayloadError,
)


def test_billing_webhook_job_contract_is_stable() -> None:
    assert BILLING_WEBHOOK_PROCESS_JOB_TYPE == "billing.webhook.process"
    assert BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION == 1


def test_payload_parses_canonical_webhook_event_id() -> None:
    webhook_event_id = uuid4()

    payload = ProcessBillingWebhookEventJobPayload.from_json(
        {
            "webhook_event_id": str(webhook_event_id),
        }
    )

    assert payload.webhook_event_id == webhook_event_id


def test_payload_accepts_uppercase_canonical_uuid() -> None:
    webhook_event_id = uuid4()

    payload = ProcessBillingWebhookEventJobPayload.from_json(
        {
            "webhook_event_id": str(webhook_event_id).upper(),
        }
    )

    assert payload.webhook_event_id == webhook_event_id


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "webhook_event_id": str(uuid4()),
            "unexpected": "value",
        },
        {
            "different_field": str(uuid4()),
        },
    ],
)
def test_payload_rejects_invalid_field_set(
    payload: JSONObject,
) -> None:
    with pytest.raises(InvalidJobPayloadError):
        ProcessBillingWebhookEventJobPayload.from_json(payload)


@pytest.mark.parametrize(
    "raw_webhook_event_id",
    [
        "",
        "not-a-uuid",
        f" {uuid4()}",
        f"{uuid4()} ",
        "{12345678-1234-1234-1234-123456789abc}",
        "00000000-0000-0000-0000-000000000000",
    ],
)
def test_payload_rejects_invalid_uuid_string(
    raw_webhook_event_id: str,
) -> None:
    with pytest.raises(InvalidJobPayloadError):
        ProcessBillingWebhookEventJobPayload.from_json(
            {
                "webhook_event_id": (raw_webhook_event_id),
            }
        )


@pytest.mark.parametrize(
    "raw_webhook_event_id",
    [
        123,
        True,
        None,
        UUID("12345678-1234-1234-1234-123456789abc"),
    ],
)
def test_payload_rejects_non_string_uuid_value(
    raw_webhook_event_id: object,
) -> None:
    payload = cast(
        JSONObject,
        {
            "webhook_event_id": (raw_webhook_event_id),
        },
    )

    with pytest.raises(InvalidJobPayloadError):
        ProcessBillingWebhookEventJobPayload.from_json(payload)
