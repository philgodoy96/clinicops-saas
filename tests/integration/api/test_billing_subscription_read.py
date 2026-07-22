from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.api.v1.billing.dependencies import (
    get_billing_read_tenant_context,
)
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    SubscriptionStatus,
)
from clinicops.billing.models import (
    BillingCustomer,
    Subscription,
)
from clinicops.db.session import get_engine
from clinicops.main import create_app
from clinicops.tenancy.models import Tenant

PERIOD_START = datetime(
    2026,
    7,
    22,
    12,
    tzinfo=UTC,
)
PERIOD_END = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)


def _persist_tenant(
    *,
    with_subscription: bool,
    price_code: str = "starter_monthly",
) -> tuple[UUID, UUID | None]:
    tenant_id = uuid4()
    subscription_id: UUID | None = None

    with Session(get_engine()) as session:
        tenant = Tenant(
            id=tenant_id,
            name=f"Billing Read Clinic {tenant_id.hex}",
        )
        session.add(tenant)

        if with_subscription:
            subscription_id = uuid4()
            billing_customer = BillingCustomer(
                id=uuid4(),
                tenant_id=tenant_id,
                provider=BillingProvider.FAKE,
                provider_customer_id=(f"fake_cus_{tenant_id.hex}"),
            )
            subscription = Subscription(
                id=subscription_id,
                tenant_id=tenant_id,
                billing_customer_id=billing_customer.id,
                provider=BillingProvider.FAKE,
                provider_subscription_id=(f"fake_sub_{tenant_id.hex}"),
                price_code=price_code,
                plan=(
                    BillingPlan.STARTER
                    if price_code.startswith("starter")
                    else BillingPlan.PROFESSIONAL
                ),
                billing_interval=BillingInterval.MONTHLY,
                currency="USD",
                unit_amount=(4900 if price_code.startswith("starter") else 9900),
                pending_price_code=None,
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=False,
                cancellation_requested_at=None,
                current_period_start=PERIOD_START,
                current_period_end=PERIOD_END,
                provider_state_version=1,
                last_provider_event_at=None,
                canceled_at=None,
            )
            session.add(billing_customer)
            session.add(subscription)

        session.commit()

    return tenant_id, subscription_id


def _cleanup_tenants(*tenant_ids: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(delete(Subscription).where(Subscription.tenant_id.in_(tenant_ids)))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id.in_(tenant_ids)))
        session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        session.commit()


def _authorized_application() -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_billing_read_tenant_context] = lambda: object()

    return application


@contextmanager
def _client() -> Iterator[TestClient]:
    application = _authorized_application()

    try:
        with TestClient(application) as client:
            yield client
    finally:
        application.dependency_overrides.clear()


def test_read_returns_persisted_subscription() -> None:
    tenant_id, subscription_id = _persist_tenant(
        with_subscription=True,
    )

    try:
        with _client() as client:
            response = client.get(f"/api/v1/tenants/{tenant_id}/billing/subscription")

        assert response.status_code == 200

        payload = response.json()

        assert payload["id"] == str(subscription_id)
        assert payload["tenant_id"] == str(tenant_id)
        assert payload["price_code"] == "starter_monthly"
        assert payload["plan"] == "starter"
        assert payload["billing_interval"] == "monthly"
        assert payload["currency"] == "USD"
        assert payload["unit_amount"] == 4900
        assert payload["status"] == "active"
        assert payload["cancel_at_period_end"] is False
        assert payload["pending_price_code"] is None
        assert "provider_customer_id" not in payload
        assert "provider_subscription_id" not in payload
        assert "provider_reference" not in payload
    finally:
        _cleanup_tenants(tenant_id)


def test_read_is_scoped_to_path_tenant() -> None:
    first_tenant_id, first_subscription_id = _persist_tenant(
        with_subscription=True,
        price_code="starter_monthly",
    )
    second_tenant_id, second_subscription_id = _persist_tenant(
        with_subscription=True,
        price_code="professional_monthly",
    )

    try:
        with _client() as client:
            response = client.get(f"/api/v1/tenants/{first_tenant_id}/billing/subscription")

        assert response.status_code == 200

        payload = response.json()

        assert payload["id"] == str(first_subscription_id)
        assert payload["tenant_id"] == str(first_tenant_id)
        assert payload["price_code"] == "starter_monthly"
        assert payload["id"] != str(second_subscription_id)
        assert payload["tenant_id"] != str(second_tenant_id)
    finally:
        _cleanup_tenants(
            first_tenant_id,
            second_tenant_id,
        )


def test_missing_subscription_returns_not_found() -> None:
    tenant_id, _ = _persist_tenant(
        with_subscription=False,
    )

    try:
        with _client() as client:
            response = client.get(f"/api/v1/tenants/{tenant_id}/billing/subscription")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")

        payload = response.json()

        assert payload["status"] == 404
        assert payload["code"] == ("billing_subscription_not_found")
        assert payload["detail"] == "The billing subscription was not found."
    finally:
        _cleanup_tenants(tenant_id)
