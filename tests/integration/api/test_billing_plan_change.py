from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.api.v1.billing.dependencies import (
    get_billing_manage_tenant_context,
)
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
)
from clinicops.billing.models import (
    BillingCustomer,
    ProviderOperation,
    Subscription,
)
from clinicops.billing.providers.contracts import (
    CreateCustomerRequest,
    CreateSubscriptionRequest,
)
from clinicops.billing.providers.fake import (
    FakePaymentProvider,
)
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
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


@dataclass(frozen=True, slots=True)
class ApiPlanChangeFixture:
    tenant_id: UUID
    subscription_id: UUID
    provider: FakePaymentProvider


def _persist_subscription(
    *,
    provider: FakePaymentProvider | None = None,
) -> ApiPlanChangeFixture:
    resolved_provider = provider or FakePaymentProvider()
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()

    provider_customer = resolved_provider.create_customer(
        CreateCustomerRequest(provider_operation_key=(build_provider_operation_key(uuid4())))
    )
    provider_subscription = resolved_provider.create_subscription(
        CreateSubscriptionRequest(
            provider_operation_key=(build_provider_operation_key(uuid4())),
            provider_customer_id=(provider_customer.provider_customer_id),
            price_code="starter_monthly",
            effective_at=PERIOD_START,
        )
    )

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=(f"Plan Change API Clinic {tenant_id.hex}"),
            )
        )
        session.add(
            BillingCustomer(
                id=billing_customer_id,
                tenant_id=tenant_id,
                provider=BillingProvider.FAKE,
                provider_customer_id=(provider_customer.provider_customer_id),
            )
        )
        session.add(
            Subscription(
                id=subscription_id,
                tenant_id=tenant_id,
                billing_customer_id=billing_customer_id,
                provider=BillingProvider.FAKE,
                provider_subscription_id=(provider_subscription.provider_subscription_id),
                price_code="starter_monthly",
                plan=BillingPlan.STARTER,
                billing_interval=(BillingInterval.MONTHLY),
                currency="USD",
                unit_amount=4900,
                pending_price_code=None,
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=False,
                cancellation_requested_at=None,
                current_period_start=(provider_subscription.current_period_start),
                current_period_end=(provider_subscription.current_period_end),
                provider_state_version=1,
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return ApiPlanChangeFixture(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        provider=resolved_provider,
    )


def _cleanup_tenants(*tenant_ids: UUID) -> None:
    with Session(get_engine()) as session:
        session.execute(
            delete(ProviderOperation).where(ProviderOperation.tenant_id.in_(tenant_ids))
        )
        session.execute(delete(Subscription).where(Subscription.tenant_id.in_(tenant_ids)))
        session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id.in_(tenant_ids)))
        session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        session.commit()


def _authorized_application(
    provider: FakePaymentProvider,
) -> FastAPI:
    application = create_app()
    application.state.payment_provider = provider
    application.dependency_overrides[get_billing_manage_tenant_context] = lambda: object()

    return application


@contextmanager
def _client(
    provider: FakePaymentProvider,
) -> Iterator[TestClient]:
    application = _authorized_application(provider)

    try:
        with TestClient(application) as client:
            yield client
    finally:
        application.dependency_overrides.clear()


def _load_subscription(
    tenant_id: UUID,
) -> Subscription:
    with Session(get_engine()) as session:
        subscription = session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )

        assert subscription is not None
        return subscription


def _load_operations(
    tenant_id: UUID,
) -> list[ProviderOperation]:
    with Session(get_engine()) as session:
        return list(
            session.scalars(
                select(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id)
            ).all()
        )


def test_plan_change_api_persists_pending_target() -> None:
    fixture = _persist_subscription()

    try:
        with _client(fixture.provider) as client:
            response = client.post(
                (f"/api/v1/tenants/{fixture.tenant_id}/billing/subscription/plan-change"),
                headers={
                    "Idempotency-Key": ("api-plan-change-request"),
                },
                json={
                    "price_code": "professional_monthly",
                },
            )

        assert response.status_code == 200

        payload = response.json()

        assert payload["id"] == str(fixture.subscription_id)
        assert payload["tenant_id"] == str(fixture.tenant_id)
        assert payload["price_code"] == ("starter_monthly")
        assert payload["plan"] == "starter"
        assert payload["pending_price_code"] == "professional_monthly"
        assert "replayed" not in payload
        assert "provider_subscription_id" not in payload
        assert "provider_reference" not in payload

        subscription = _load_subscription(fixture.tenant_id)
        operations = _load_operations(fixture.tenant_id)

        assert subscription.price_code == ("starter_monthly")
        assert subscription.pending_price_code == "professional_monthly"
        assert subscription.provider_state_version == 2
        assert len(operations) == 1
        assert operations[0].operation_type is ProviderOperationType.CHANGE_PLAN
        assert operations[0].status is ProviderOperationStatus.SUCCEEDED
    finally:
        _cleanup_tenants(fixture.tenant_id)


def test_plan_change_api_replays_same_client_key() -> None:
    fixture = _persist_subscription()
    path = f"/api/v1/tenants/{fixture.tenant_id}/billing/subscription/plan-change"
    headers = {
        "Idempotency-Key": "api-plan-change-replay",
    }
    body = {
        "price_code": "professional_monthly",
    }

    try:
        with _client(fixture.provider) as client:
            first = client.post(
                path,
                headers=headers,
                json=body,
            )
            replayed = client.post(
                path,
                headers=headers,
                json=body,
            )

        assert first.status_code == 200
        assert replayed.status_code == 200
        assert replayed.json() == first.json()

        operations = _load_operations(fixture.tenant_id)

        assert len(operations) == 1
        assert operations[0].attempt_count == 1
    finally:
        _cleanup_tenants(fixture.tenant_id)


def test_plan_change_api_is_scoped_to_path_tenant() -> None:
    provider = FakePaymentProvider()
    selected = _persist_subscription(provider=provider)
    untouched = _persist_subscription(provider=provider)

    try:
        with _client(provider) as client:
            response = client.post(
                (f"/api/v1/tenants/{selected.tenant_id}/billing/subscription/plan-change"),
                headers={
                    "Idempotency-Key": ("tenant-scoped-plan-change"),
                },
                json={
                    "price_code": "professional_monthly",
                },
            )

        assert response.status_code == 200

        selected_subscription = _load_subscription(selected.tenant_id)
        untouched_subscription = _load_subscription(untouched.tenant_id)

        assert selected_subscription.pending_price_code == "professional_monthly"
        assert untouched_subscription.pending_price_code is None
        assert untouched_subscription.provider_state_version == 1
        assert len(_load_operations(selected.tenant_id)) == 1
        assert len(_load_operations(untouched.tenant_id)) == 0
    finally:
        _cleanup_tenants(
            selected.tenant_id,
            untouched.tenant_id,
        )
