from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.billing.enums import BillingProvider
from clinicops.billing.exceptions import (
    BillingProviderSubscriptionAlreadyLinkedError,
    BillingSubscriptionAlreadyExistsError,
)
from clinicops.billing.models import Subscription

_TENANT_SUBSCRIPTION_CONSTRAINT = "uq_subscriptions_tenant_id"
_PROVIDER_SUBSCRIPTION_CONSTRAINT = "uq_subscriptions_provider_subscription_id"


class SubscriptionRepository:
    """Persist and lock tenant subscription records."""

    def get_by_tenant_id(
        self,
        session: Session,
        *,
        tenant_id: UUID,
    ) -> Subscription | None:
        statement = select(Subscription).where(Subscription.tenant_id == tenant_id)

        return session.scalar(statement)

    def get_by_tenant_id_for_update(
        self,
        session: Session,
        *,
        tenant_id: UUID,
    ) -> Subscription | None:
        statement = (
            select(Subscription)
            .where(Subscription.tenant_id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def get_by_provider_subscription_id_for_update(
        self,
        session: Session,
        *,
        provider: BillingProvider,
        provider_subscription_id: str,
    ) -> Subscription | None:
        statement = (
            select(Subscription)
            .where(
                Subscription.provider == provider,
                Subscription.provider_subscription_id == provider_subscription_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        subscription_id: UUID,
    ) -> Subscription | None:
        statement = (
            select(Subscription)
            .where(Subscription.id == subscription_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def add_and_flush(
        self,
        session: Session,
        subscription: Subscription,
    ) -> Subscription:
        session.add(subscription)

        try:
            session.flush()
        except IntegrityError as error:
            constraint_name = _constraint_name(error)

            if constraint_name == _TENANT_SUBSCRIPTION_CONSTRAINT:
                raise BillingSubscriptionAlreadyExistsError() from error

            if constraint_name == _PROVIDER_SUBSCRIPTION_CONSTRAINT:
                raise (BillingProviderSubscriptionAlreadyLinkedError()) from error

            raise

        return subscription

    def flush(self, session: Session) -> None:
        session.flush()


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
