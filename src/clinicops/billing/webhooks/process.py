from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingProvider,
    BillingWebhookEventStatus,
    BillingWebhookEventType,
    BillingWebhookProcessingOutcome,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventNotFoundError,
    BillingWebhookEventProcessingConflictError,
    BillingWebhookEventRetryableFailureError,
    BillingWebhookEventTerminalFailureError,
)
from clinicops.billing.models import BillingWebhookEvent
from clinicops.billing.repositories import (
    BillingWebhookEventRepository,
    SubscriptionRepository,
)
from clinicops.billing.webhooks.handlers import (
    BillingWebhookHandlerResult,
    BillingWebhookTerminalProcessingError,
    apply_billing_renewal_event,
)
from clinicops.core.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class ProcessBillingWebhookEventCommand:
    """Identify one durably ingested billing event to process."""

    webhook_event_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.webhook_event_id, UUID):
            raise TypeError("The billing webhook event ID must be a UUID.")


@dataclass(frozen=True, slots=True)
class BillingWebhookEventClaim:
    """Persisted ownership or replay state for one webhook event."""

    webhook_event_id: UUID
    provider: BillingProvider
    provider_event_id: str
    provider_subscription_id: str
    provider_state_version: int
    event_type: BillingWebhookEventType
    status: BillingWebhookEventStatus
    processing_attempt_count: int
    replayed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.webhook_event_id, UUID):
            raise TypeError("The billing webhook event ID must be a UUID.")

        normalized_provider_event_id = self.provider_event_id.strip()
        normalized_subscription_id = self.provider_subscription_id.strip()

        if not normalized_provider_event_id:
            raise ValueError("The provider event ID must not be empty.")

        if not normalized_subscription_id:
            raise ValueError("The provider subscription ID must not be empty.")

        object.__setattr__(
            self,
            "provider_event_id",
            normalized_provider_event_id,
        )
        object.__setattr__(
            self,
            "provider_subscription_id",
            normalized_subscription_id,
        )

        if self.provider_state_version < 1:
            raise ValueError("The provider state version must be positive.")

        if self.processing_attempt_count < 1:
            raise ValueError(
                "A billing webhook processing claim must include at least one attempt."
            )

        replay_statuses = {
            BillingWebhookEventStatus.PROCESSED,
            BillingWebhookEventStatus.IGNORED,
        }

        if self.replayed:
            if self.status not in replay_statuses:
                raise ValueError(
                    "A replayed billing webhook claim must reference a completed event."
                )
        elif self.status is not BillingWebhookEventStatus.PROCESSING:
            raise ValueError("A new billing webhook claim must persist the processing state.")


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


class ClaimBillingWebhookEventService:
    """Claim one persisted event for processing or recognize replay."""

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
        command: ProcessBillingWebhookEventCommand,
    ) -> BillingWebhookEventClaim:
        """Lock and claim one event without committing the transaction."""

        event = self._webhook_event_repository.get_by_id_for_update(
            session,
            webhook_event_id=command.webhook_event_id,
        )

        if event is None:
            raise BillingWebhookEventNotFoundError()

        if event.status is BillingWebhookEventStatus.PROCESSING:
            raise BillingWebhookEventProcessingConflictError()

        if event.status is BillingWebhookEventStatus.FAILED_TERMINAL:
            failure_code = event.failure_code

            if failure_code is None:
                raise RuntimeError("A terminal billing webhook event must persist a failure code.")

            raise BillingWebhookEventTerminalFailureError(
                failure_code=failure_code,
            )

        if event.status in {
            BillingWebhookEventStatus.PROCESSED,
            BillingWebhookEventStatus.IGNORED,
        }:
            return _to_claim(
                event,
                event_type=_resolve_event_type(event),
                replayed=True,
            )

        if event.status not in {
            BillingWebhookEventStatus.RECEIVED,
            BillingWebhookEventStatus.FAILED_RETRYABLE,
        }:
            raise RuntimeError("The billing webhook event has an unsupported processing status.")

        event.status = BillingWebhookEventStatus.PROCESSING
        event.processing_attempt_count += 1
        event.processed_at = None
        event.failure_code = None
        event.failure_message = None

        self._webhook_event_repository.flush(
            session,
        )

        return _to_claim(
            event,
            event_type=_resolve_event_type(event),
            replayed=False,
        )


class ProcessBillingWebhookEventService:
    """Claim and apply one persisted billing event with durable phases."""

    def __init__(
        self,
        *,
        webhook_event_repository: (BillingWebhookEventRepository | None) = None,
        subscription_repository: (SubscriptionRepository | None) = None,
        clock: Clock | None = None,
    ) -> None:
        self._webhook_event_repository = (
            webhook_event_repository
            if webhook_event_repository is not None
            else BillingWebhookEventRepository()
        )
        self._subscription_repository = (
            subscription_repository
            if subscription_repository is not None
            else SubscriptionRepository()
        )
        self._clock = clock if clock is not None else SystemClock()
        self._claim_service = ClaimBillingWebhookEventService(
            webhook_event_repository=(self._webhook_event_repository)
        )

    def execute(
        self,
        session: Session,
        command: ProcessBillingWebhookEventCommand,
    ) -> ProcessedBillingWebhookEvent:
        """Persist a processing claim before applying local state."""

        claim = self._claim_service.execute(
            session,
            command,
        )
        session.commit()

        return self._complete_processing(
            session=session,
            claim=claim,
        )

    def _complete_processing(
        self,
        *,
        session: Session,
        claim: BillingWebhookEventClaim,
    ) -> ProcessedBillingWebhookEvent:
        event = self._webhook_event_repository.get_by_id_for_update(
            session,
            webhook_event_id=claim.webhook_event_id,
        )

        if event is None:
            raise BillingWebhookEventNotFoundError()

        if claim.replayed:
            return self._replay_completed_event(
                session=session,
                claim=claim,
                event=event,
            )

        if (
            event.status is not BillingWebhookEventStatus.PROCESSING
            or event.processing_attempt_count != claim.processing_attempt_count
        ):
            raise BillingWebhookEventProcessingConflictError()

        subscription = self._subscription_repository.get_by_provider_subscription_id_for_update(
            session,
            provider=claim.provider,
            provider_subscription_id=(claim.provider_subscription_id),
        )

        if subscription is None:
            self._persist_retryable_failure(
                session=session,
                event=event,
                failure_code=("billing_webhook_subscription_not_found"),
                failure_message=("No local subscription matches the provider event identity."),
            )
            raise BillingWebhookEventRetryableFailureError(
                failure_code=("billing_webhook_subscription_not_found")
            )

        if claim.event_type is not BillingWebhookEventType.SUBSCRIPTION_RENEWED:
            self._persist_retryable_failure(
                session=session,
                event=event,
                failure_code="handler_not_registered",
                failure_message=(
                    "No processing handler is registered for the persisted webhook event type."
                ),
            )
            raise BillingWebhookEventRetryableFailureError(failure_code="handler_not_registered")

        try:
            handler_result = apply_billing_renewal_event(
                event=event,
                subscription=subscription,
            )
        except BillingWebhookTerminalProcessingError as error:
            self._persist_terminal_failure(
                session=session,
                event=event,
                error=error,
            )
            raise BillingWebhookEventTerminalFailureError(
                failure_code=error.failure_code,
            ) from error

        self._persist_success(
            session=session,
            event=event,
            handler_result=handler_result,
        )

        return _to_processed_result(
            event=event,
            event_type=claim.event_type,
            subscription_id=(handler_result.subscription_id),
            outcome=handler_result.outcome,
        )

    def _replay_completed_event(
        self,
        *,
        session: Session,
        claim: BillingWebhookEventClaim,
        event: BillingWebhookEvent,
    ) -> ProcessedBillingWebhookEvent:
        subscription = self._subscription_repository.get_by_provider_subscription_id_for_update(
            session,
            provider=claim.provider,
            provider_subscription_id=(claim.provider_subscription_id),
        )

        if subscription is None:
            session.rollback()
            raise RuntimeError(
                "A completed billing webhook event must resolve its local subscription."
            )

        outcome = (
            BillingWebhookProcessingOutcome.APPLIED
            if event.status is BillingWebhookEventStatus.PROCESSED
            else BillingWebhookProcessingOutcome.IGNORED
        )
        result = _to_processed_result(
            event=event,
            event_type=claim.event_type,
            subscription_id=subscription.id,
            outcome=outcome,
        )
        session.commit()

        return result

    def _persist_success(
        self,
        *,
        session: Session,
        event: BillingWebhookEvent,
        handler_result: BillingWebhookHandlerResult,
    ) -> None:
        event.status = {
            BillingWebhookProcessingOutcome.APPLIED: (BillingWebhookEventStatus.PROCESSED),
            BillingWebhookProcessingOutcome.IGNORED: (BillingWebhookEventStatus.IGNORED),
        }[handler_result.outcome]
        event.processed_at = self._clock.now()
        event.failure_code = None
        event.failure_message = None

        self._subscription_repository.flush(session)
        self._webhook_event_repository.flush(session)
        session.commit()

    def _persist_retryable_failure(
        self,
        *,
        session: Session,
        event: BillingWebhookEvent,
        failure_code: str,
        failure_message: str,
    ) -> None:
        event.status = BillingWebhookEventStatus.FAILED_RETRYABLE
        event.processed_at = None
        event.failure_code = failure_code
        event.failure_message = failure_message[:512]

        self._webhook_event_repository.flush(session)
        session.commit()

    def _persist_terminal_failure(
        self,
        *,
        session: Session,
        event: BillingWebhookEvent,
        error: BillingWebhookTerminalProcessingError,
    ) -> None:
        event.status = BillingWebhookEventStatus.FAILED_TERMINAL
        event.processed_at = self._clock.now()
        event.failure_code = error.failure_code
        event.failure_message = error.internal_message[:512]

        self._webhook_event_repository.flush(session)
        session.commit()


def _resolve_event_type(
    event: BillingWebhookEvent,
) -> BillingWebhookEventType:
    try:
        return BillingWebhookEventType(event.event_type)
    except ValueError as error:
        raise RuntimeError("The persisted billing webhook event type is unsupported.") from error


def _to_claim(
    event: BillingWebhookEvent,
    *,
    event_type: BillingWebhookEventType,
    replayed: bool,
) -> BillingWebhookEventClaim:
    return BillingWebhookEventClaim(
        webhook_event_id=event.id,
        provider=event.provider,
        provider_event_id=event.provider_event_id,
        provider_subscription_id=(event.provider_subscription_id),
        provider_state_version=(event.provider_state_version),
        event_type=event_type,
        status=event.status,
        processing_attempt_count=(event.processing_attempt_count),
        replayed=replayed,
    )


def _to_processed_result(
    *,
    event: BillingWebhookEvent,
    event_type: BillingWebhookEventType,
    subscription_id: UUID,
    outcome: BillingWebhookProcessingOutcome,
) -> ProcessedBillingWebhookEvent:
    return ProcessedBillingWebhookEvent(
        webhook_event_id=event.id,
        provider_event_id=event.provider_event_id,
        event_type=event_type,
        status=event.status,
        outcome=outcome,
        subscription_id=subscription_id,
        processing_attempt_count=(event.processing_attempt_count),
    )
