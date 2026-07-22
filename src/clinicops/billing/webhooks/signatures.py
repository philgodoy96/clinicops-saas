from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest, new
from string import hexdigits

from clinicops.billing.exceptions import (
    BillingWebhookAuthenticationError,
    BillingWebhookPayloadTooLargeError,
)

DEFAULT_BILLING_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS = 300
MAX_BILLING_WEBHOOK_PAYLOAD_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class BillingWebhookSignature:
    """Parsed timestamped HMAC signature metadata."""

    timestamp: int
    digest: str


def parse_billing_webhook_signature(
    signature_header: str | None,
) -> BillingWebhookSignature:
    """Parse the fake provider's timestamped signature header."""

    if signature_header is None or not signature_header.strip():
        raise BillingWebhookAuthenticationError("The billing webhook signature header is missing.")

    values: dict[str, str] = {}

    for component in signature_header.split(","):
        key, separator, value = component.strip().partition("=")

        if not separator or not key or not value or key in values:
            raise BillingWebhookAuthenticationError(
                "The billing webhook signature header is malformed."
            )

        values[key] = value

    if set(values) != {"t", "v1"}:
        raise BillingWebhookAuthenticationError(
            "The billing webhook signature header has unsupported fields."
        )

    try:
        timestamp = int(values["t"])
    except ValueError as error:
        raise BillingWebhookAuthenticationError(
            "The billing webhook signature timestamp is invalid."
        ) from error

    if timestamp <= 0:
        raise BillingWebhookAuthenticationError(
            "The billing webhook signature timestamp must be positive."
        )

    digest = values["v1"].lower()

    if len(digest) != 64 or any(character not in hexdigits for character in digest):
        raise BillingWebhookAuthenticationError("The billing webhook signature digest is invalid.")

    return BillingWebhookSignature(
        timestamp=timestamp,
        digest=digest,
    )


def sign_billing_webhook_payload(
    *,
    raw_body: bytes,
    secret: str,
    timestamp: int,
) -> str:
    """Create the canonical fake-provider webhook signature header."""

    secret_bytes = _require_secret(secret)
    message = _build_signed_message(
        raw_body=raw_body,
        timestamp=timestamp,
    )
    digest = new(
        secret_bytes,
        message,
        sha256,
    ).hexdigest()

    return f"t={timestamp},v1={digest}"


def verify_billing_webhook_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
    now: datetime,
    tolerance_seconds: int = (DEFAULT_BILLING_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS),
    max_payload_bytes: int = (MAX_BILLING_WEBHOOK_PAYLOAD_BYTES),
) -> BillingWebhookSignature:
    """Authenticate raw webhook bytes before JSON parsing."""

    if max_payload_bytes <= 0:
        raise ValueError("The billing webhook payload limit must be positive.")

    if len(raw_body) > max_payload_bytes:
        raise BillingWebhookPayloadTooLargeError(
            actual_size=len(raw_body),
            maximum_size=max_payload_bytes,
        )

    if tolerance_seconds < 0:
        raise ValueError("The billing webhook signature tolerance cannot be negative.")

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The billing webhook verification clock must be timezone-aware.")

    parsed = parse_billing_webhook_signature(signature_header)
    current_timestamp = int(now.timestamp())

    if abs(current_timestamp - parsed.timestamp) > tolerance_seconds:
        raise BillingWebhookAuthenticationError(
            "The billing webhook signature timestamp is outside the accepted tolerance."
        )

    secret_bytes = _require_secret(secret)
    expected_digest = new(
        secret_bytes,
        _build_signed_message(
            raw_body=raw_body,
            timestamp=parsed.timestamp,
        ),
        sha256,
    ).hexdigest()

    if not compare_digest(
        expected_digest,
        parsed.digest,
    ):
        raise BillingWebhookAuthenticationError(
            "The billing webhook signature does not match the request payload."
        )

    return parsed


def _build_signed_message(
    *,
    raw_body: bytes,
    timestamp: int,
) -> bytes:
    return str(timestamp).encode("ascii") + b"." + raw_body


def _require_secret(secret: str) -> bytes:
    normalized = secret.strip()

    if not normalized:
        raise RuntimeError("The billing webhook secret is not configured.")

    return normalized.encode("utf-8")
