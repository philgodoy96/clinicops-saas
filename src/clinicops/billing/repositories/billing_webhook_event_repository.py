from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.billing.enums import BillingProvider
from clinicops.billing.exceptions import (
    BillingWebhookEventAlreadyExistsError,
)
from clinicops.billing.models import BillingWebhookEvent

_PROVIDER_EVENT_CONSTRAINT = "uq_billing_webhook_events_provider_event"


class BillingWebhookEventRepository:
    """Persist and lock verified billing webhook events."""

    def get_by_provider_event_id(
        self,
        session: Session,
        *,
        provider: BillingProvider,
        provider_event_id: str,
    ) -> BillingWebhookEvent | None:
        statement = select(BillingWebhookEvent).where(
            BillingWebhookEvent.provider == provider,
            BillingWebhookEvent.provider_event_id == provider_event_id,
        )

        return session.scalar(statement)

    def get_by_provider_event_id_for_update(
        self,
        session: Session,
        *,
        provider: BillingProvider,
        provider_event_id: str,
    ) -> BillingWebhookEvent | None:
        statement = (
            select(BillingWebhookEvent)
            .where(
                BillingWebhookEvent.provider == provider,
                BillingWebhookEvent.provider_event_id == provider_event_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        webhook_event_id: UUID,
    ) -> BillingWebhookEvent | None:
        statement = (
            select(BillingWebhookEvent)
            .where(BillingWebhookEvent.id == webhook_event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def add_and_flush(
        self,
        session: Session,
        event: BillingWebhookEvent,
    ) -> BillingWebhookEvent:
        session.add(event)

        try:
            session.flush()
        except IntegrityError as error:
            if _constraint_name(error) == _PROVIDER_EVENT_CONSTRAINT:
                raise BillingWebhookEventAlreadyExistsError() from error

            raise

        return event

    def flush(self, session: Session) -> None:
        session.flush()


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
