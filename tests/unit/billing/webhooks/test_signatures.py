from datetime import UTC, datetime

import pytest

from clinicops.billing.exceptions import (
    BillingWebhookAuthenticationError,
    BillingWebhookPayloadTooLargeError,
)
from clinicops.billing.webhooks.signatures import (
    BillingWebhookSignature,
    parse_billing_webhook_signature,
    sign_billing_webhook_payload,
    verify_billing_webhook_signature,
)

SECRET = "local-webhook-secret"
NOW = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
TIMESTAMP = int(NOW.timestamp())
RAW_BODY = b'{"id":"evt_01","type":"subscription.renewed"}'


def test_signature_round_trip_authenticates_raw_body() -> None:
    header = sign_billing_webhook_payload(
        raw_body=RAW_BODY,
        secret=SECRET,
        timestamp=TIMESTAMP,
    )

    parsed = verify_billing_webhook_signature(
        raw_body=RAW_BODY,
        signature_header=header,
        secret=SECRET,
        now=NOW,
    )

    assert parsed == BillingWebhookSignature(
        timestamp=TIMESTAMP,
        digest=header.split("v1=", 1)[1],
    )


def test_signature_covers_exact_raw_bytes() -> None:
    header = sign_billing_webhook_payload(
        raw_body=RAW_BODY,
        secret=SECRET,
        timestamp=TIMESTAMP,
    )

    with pytest.raises(BillingWebhookAuthenticationError):
        verify_billing_webhook_signature(
            raw_body=RAW_BODY + b" ",
            signature_header=header,
            secret=SECRET,
            now=NOW,
        )


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "v1=abc",
        "t=abc,v1=" + ("a" * 64),
        "t=0,v1=" + ("a" * 64),
        "t=1,v1=not-hex",
        "t=1,v1=" + ("a" * 64) + ",v1=" + ("b" * 64),
        "t=1,v1=" + ("a" * 64) + ",unexpected=value",
    ],
)
def test_malformed_signature_header_is_rejected(
    header: str | None,
) -> None:
    with pytest.raises(BillingWebhookAuthenticationError):
        parse_billing_webhook_signature(header)


@pytest.mark.parametrize(
    "timestamp_offset",
    [
        -301,
        301,
    ],
)
def test_signature_outside_tolerance_is_rejected(
    timestamp_offset: int,
) -> None:
    timestamp = TIMESTAMP + timestamp_offset
    header = sign_billing_webhook_payload(
        raw_body=RAW_BODY,
        secret=SECRET,
        timestamp=timestamp,
    )

    with pytest.raises(BillingWebhookAuthenticationError):
        verify_billing_webhook_signature(
            raw_body=RAW_BODY,
            signature_header=header,
            secret=SECRET,
            now=NOW,
            tolerance_seconds=300,
        )


def test_signature_at_tolerance_boundary_is_accepted() -> None:
    timestamp = TIMESTAMP - 300
    header = sign_billing_webhook_payload(
        raw_body=RAW_BODY,
        secret=SECRET,
        timestamp=timestamp,
    )

    parsed = verify_billing_webhook_signature(
        raw_body=RAW_BODY,
        signature_header=header,
        secret=SECRET,
        now=NOW,
        tolerance_seconds=300,
    )

    assert parsed.timestamp == timestamp


def test_oversized_payload_is_rejected() -> None:
    raw_body = b"x" * 11

    with pytest.raises(BillingWebhookPayloadTooLargeError):
        verify_billing_webhook_signature(
            raw_body=raw_body,
            signature_header=None,
            secret=SECRET,
            now=NOW,
            max_payload_bytes=10,
        )


def test_payload_at_size_limit_is_accepted() -> None:
    raw_body = b"x" * 10
    header = sign_billing_webhook_payload(
        raw_body=raw_body,
        secret=SECRET,
        timestamp=TIMESTAMP,
    )

    verify_billing_webhook_signature(
        raw_body=raw_body,
        signature_header=header,
        secret=SECRET,
        now=NOW,
        max_payload_bytes=10,
    )


def test_verification_requires_timezone_aware_clock() -> None:
    header = sign_billing_webhook_payload(
        raw_body=RAW_BODY,
        secret=SECRET,
        timestamp=TIMESTAMP,
    )

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        verify_billing_webhook_signature(
            raw_body=RAW_BODY,
            signature_header=header,
            secret=SECRET,
            now=datetime(2026, 8, 22, 12),
        )


def test_signing_requires_configured_secret() -> None:
    with pytest.raises(
        RuntimeError,
        match="not configured",
    ):
        sign_billing_webhook_payload(
            raw_body=RAW_BODY,
            secret=" ",
            timestamp=TIMESTAMP,
        )
