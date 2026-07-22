from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Request,
    status,
)
from pydantic import BaseModel

from clinicops.api.dependencies import (
    ApplicationSettingsDependency,
    DatabaseSessionDependency,
)
from clinicops.billing.enums import BillingProvider
from clinicops.billing.exceptions import (
    BillingWebhookProviderNotFoundError,
)
from clinicops.billing.webhooks.ingest import (
    IngestBillingWebhookCommand,
    IngestBillingWebhookService,
)
from clinicops.billing.webhooks.signatures import (
    verify_billing_webhook_signature,
)
from clinicops.core.clock import Clock, SystemClock

router = APIRouter(
    prefix="/billing/webhooks",
    tags=["billing-webhooks"],
)


class BillingWebhookReceiptResponse(BaseModel):
    """Public acknowledgement for a durably received webhook."""

    received: Literal[True] = True


@dataclass(frozen=True, slots=True)
class AuthenticatedBillingWebhook:
    """Raw provider delivery authenticated before JSON parsing."""

    provider: BillingProvider
    raw_body: bytes
    signature_timestamp: int
    correlation_id: str | None


BillingWebhookSignatureHeader = Annotated[
    str | None,
    Header(alias="X-Billing-Signature"),
]


def get_billing_webhook_clock() -> Clock:
    """Return the system clock used for signature freshness checks."""

    return SystemClock()


BillingWebhookClockDependency = Annotated[
    Clock,
    Depends(get_billing_webhook_clock),
]


def get_ingest_billing_webhook_service() -> IngestBillingWebhookService:
    """Build the authenticated webhook ingestion service."""

    return IngestBillingWebhookService()


IngestBillingWebhookServiceDependency = Annotated[
    IngestBillingWebhookService,
    Depends(get_ingest_billing_webhook_service),
]


async def get_authenticated_billing_webhook(
    provider: str,
    request: Request,
    settings: ApplicationSettingsDependency,
    clock: BillingWebhookClockDependency,
    signature_header: BillingWebhookSignatureHeader = None,
) -> AuthenticatedBillingWebhook:
    """Authenticate exact request bytes before event parsing."""

    resolved_provider = _resolve_provider(provider)
    raw_body = await request.body()
    signature = verify_billing_webhook_signature(
        raw_body=raw_body,
        signature_header=signature_header,
        secret=(settings.billing_webhook_secret.get_secret_value()),
        now=clock.now(),
        tolerance_seconds=(settings.billing_webhook_signature_tolerance_seconds),
        max_payload_bytes=(settings.billing_webhook_max_payload_bytes),
    )
    correlation_id = getattr(
        request.state,
        "correlation_id",
        None,
    )

    if not isinstance(correlation_id, str):
        correlation_id = None

    return AuthenticatedBillingWebhook(
        provider=resolved_provider,
        raw_body=raw_body,
        signature_timestamp=signature.timestamp,
        correlation_id=correlation_id,
    )


AuthenticatedBillingWebhookDependency = Annotated[
    AuthenticatedBillingWebhook,
    Depends(get_authenticated_billing_webhook),
]


@router.post(
    "/{provider}",
    response_model=BillingWebhookReceiptResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest an authenticated billing webhook",
)
def ingest_billing_webhook(
    authenticated_webhook: (AuthenticatedBillingWebhookDependency),
    session: DatabaseSessionDependency,
    service: IngestBillingWebhookServiceDependency,
) -> BillingWebhookReceiptResponse:
    """Commit durable receipt before acknowledging the provider."""

    service.execute(
        session,
        IngestBillingWebhookCommand(
            provider=authenticated_webhook.provider,
            raw_body=authenticated_webhook.raw_body,
            signature_timestamp=(authenticated_webhook.signature_timestamp),
            correlation_id=(authenticated_webhook.correlation_id),
        ),
    )
    session.commit()

    return BillingWebhookReceiptResponse()


def _resolve_provider(
    provider: str,
) -> BillingProvider:
    normalized_provider = provider.strip().lower()

    try:
        return BillingProvider(normalized_provider)
    except ValueError as error:
        raise BillingWebhookProviderNotFoundError(
            provider=normalized_provider,
        ) from error
