from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.billing.enums import BillingProvider
from clinicops.billing.exceptions import (
    BillingCustomerAlreadyExistsError,
    BillingProviderCustomerAlreadyLinkedError,
)
from clinicops.billing.models import BillingCustomer

_TENANT_PROVIDER_CONSTRAINT = "uq_billing_customers_tenant_id_provider"
_PROVIDER_CUSTOMER_CONSTRAINT = "uq_billing_customers_provider_customer_id"


class BillingCustomerRepository:
    """Persist and lock tenant billing-customer records."""

    def get_by_tenant_and_provider(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        provider: BillingProvider,
    ) -> BillingCustomer | None:
        statement = select(BillingCustomer).where(
            BillingCustomer.tenant_id == tenant_id,
            BillingCustomer.provider == provider,
        )

        return session.scalar(statement)

    def get_by_tenant_and_provider_for_update(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        provider: BillingProvider,
    ) -> BillingCustomer | None:
        statement = (
            select(BillingCustomer)
            .where(
                BillingCustomer.tenant_id == tenant_id,
                BillingCustomer.provider == provider,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        billing_customer_id: UUID,
    ) -> BillingCustomer | None:
        statement = (
            select(BillingCustomer)
            .where(BillingCustomer.id == billing_customer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def add_and_flush(
        self,
        session: Session,
        customer: BillingCustomer,
    ) -> BillingCustomer:
        session.add(customer)

        try:
            session.flush()
        except IntegrityError as error:
            constraint_name = _constraint_name(error)

            if constraint_name == _TENANT_PROVIDER_CONSTRAINT:
                raise BillingCustomerAlreadyExistsError() from error

            if constraint_name == _PROVIDER_CUSTOMER_CONSTRAINT:
                raise BillingProviderCustomerAlreadyLinkedError() from error

            raise

        return customer

    def flush(self, session: Session) -> None:
        session.flush()


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
