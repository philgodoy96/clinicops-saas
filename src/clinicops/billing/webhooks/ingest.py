from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingProvider,
    BillingWebhookEventStatus,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventAlreadyExistsError,
    BillingWebhookEventConflictError,
    BillingWebhookPayloadInvalidError,
)
from clinicops.billing.models import BillingWebhookEvent
from clinicops.billing.repositories import (
    BillingWebhookEventRepository,
)
from clinicops.billing.webhooks.contracts import (
    BillingWebhookEventEnvelope,
)


@dataclass(frozen=True, slots=True)
class IngestBillingWebhookCommand:
    """Authenticated raw webhook input ready for durable ingestion."""

    provider: BillingProvider
    raw_body: bytes
    signature_timestamp: int
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw_body, bytes):
            raise TypeError("The billing webhook raw body must be bytes.")

        if self.signature_timestamp <= 0:
            raise ValueError("The billing webhook signature timestamp must be positive.")

        if self.correlation_id is not None:
            normalized_correlation_id = self.correlation_id.strip()

            object.__setattr__(
                self,
                "correlation_id",
                normalized_correlation_id or None,
            )


@dataclass(frozen=True, slots=True)
class IngestedBillingWebhook:
    """Durably reserved webhook event ingestion result."""

    webhook_event_id: UUID
    provider: BillingProvider
    provider_event_id: str
    status: BillingWebhookEventStatus
    duplicate: bool


class IngestBillingWebhookService:
    """Persist authenticated billing events without processing them."""

    def __init__(
        self,
        *,
        webhook_event_repository: (BillingWebhookEventRepository | None) = None,
    ) -> None:
        self._webhook_event_repository = (
            webhook_event_repository
            if webhook_event_repository is not None
            else BillingWebhookEventRepository()
        )

    def execute(
        self,
        session: Session,
        command: IngestBillingWebhookCommand,
    ) -> IngestedBillingWebhook:
        """Reserve or replay one immutable provider event.

        The caller owns the successful transaction commit. A rollback is
        performed only when recovering from a concurrent unique conflict.
        """

        envelope = _parse_event(command.raw_body)
        payload_sha256 = sha256(command.raw_body).hexdigest()

        existing = self._webhook_event_repository.get_by_provider_event_id(
            session,
            provider=command.provider,
            provider_event_id=(envelope.provider_event_id),
        )
        if existing is not None:
            return self._resolve_existing(
                existing=existing,
                payload_sha256=payload_sha256,
            )

        event = BillingWebhookEvent(
            provider=command.provider,
            provider_event_id=(envelope.provider_event_id),
            event_type=envelope.event_type.value,
            provider_subscription_id=(envelope.data.provider_subscription_id),
            provider_created_at=envelope.created_at,
            provider_state_version=(envelope.data.provider_state_version),
            payload=envelope.model_dump(
                mode="json",
                by_alias=True,
            ),
            payload_sha256=payload_sha256,
            signature_timestamp=(command.signature_timestamp),
            status=BillingWebhookEventStatus.RECEIVED,
            processing_attempt_count=0,
            processed_at=None,
            failure_code=None,
            failure_message=None,
            correlation_id=command.correlation_id,
        )

        try:
            self._webhook_event_repository.add_and_flush(
                session,
                event,
            )
        except BillingWebhookEventAlreadyExistsError:
            session.rollback()
            return self._recover_concurrent_duplicate(
                session=session,
                provider=command.provider,
                provider_event_id=(envelope.provider_event_id),
                payload_sha256=payload_sha256,
            )

        return _to_result(
            event,
            duplicate=False,
        )

    def _recover_concurrent_duplicate(
        self,
        *,
        session: Session,
        provider: BillingProvider,
        provider_event_id: str,
        payload_sha256: str,
    ) -> IngestedBillingWebhook:
        existing = self._webhook_event_repository.get_by_provider_event_id(
            session,
            provider=provider,
            provider_event_id=provider_event_id,
        )
        if existing is None:
            raise RuntimeError(
                "The concurrent billing webhook event "
                "could not be reloaded after a uniqueness conflict."
            )

        return self._resolve_existing(
            existing=existing,
            payload_sha256=payload_sha256,
        )

    @staticmethod
    def _resolve_existing(
        *,
        existing: BillingWebhookEvent,
        payload_sha256: str,
    ) -> IngestedBillingWebhook:
        if existing.payload_sha256 != payload_sha256:
            raise BillingWebhookEventConflictError(provider_event_id=(existing.provider_event_id))

        return _to_result(
            existing,
            duplicate=True,
        )


def _parse_event(
    raw_body: bytes,
) -> BillingWebhookEventEnvelope:
    try:
        return BillingWebhookEventEnvelope.model_validate_json(raw_body)
    except ValidationError as error:
        raise BillingWebhookPayloadInvalidError(
            internal_message=(
                "The authenticated billing webhook body is not a valid canonical event."
            )
        ) from error


def _to_result(
    event: BillingWebhookEvent,
    *,
    duplicate: bool,
) -> IngestedBillingWebhook:
    return IngestedBillingWebhook(
        webhook_event_id=event.id,
        provider=event.provider,
        provider_event_id=event.provider_event_id,
        status=event.status,
        duplicate=duplicate,
    )
