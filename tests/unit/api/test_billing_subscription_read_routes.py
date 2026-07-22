from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from clinicops.api.dependencies import (
    get_database_session,
)
from clinicops.api.errors import register_exception_handlers
from clinicops.api.v1.billing.dependencies import (
    get_billing_read_tenant_context,
    get_billing_subscription_query_service,
)
from clinicops.api.v1.billing.routes import router
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingSubscriptionNotFoundError,
)
from clinicops.billing.services.get_subscription import (
    BillingSubscriptionDetails,
    GetBillingSubscriptionQuery,
)

TENANT_ID = UUID("5c9d9f0b-bffd-4519-85e4-817f365daee8")
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


class RecordingSubscriptionQueryService:
    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self._error = error
        self.query: GetBillingSubscriptionQuery | None = None

    def execute(
        self,
        session: Session,
        query: GetBillingSubscriptionQuery,
    ) -> BillingSubscriptionDetails:
        self.query = query

        if self._error is not None:
            raise self._error

        return BillingSubscriptionDetails(
            id=uuid4(),
            tenant_id=query.tenant_id,
            price_code="starter_monthly",
            plan=BillingPlan.STARTER,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            unit_amount=4900,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=PERIOD_START,
            current_period_end=PERIOD_END,
            cancel_at_period_end=False,
            cancellation_requested_at=None,
            canceled_at=None,
            pending_price_code=None,
            created_at=PERIOD_START,
            updated_at=PERIOD_START,
        )


def _database_session() -> Iterator[Session]:
    yield cast(Session, object())


def _build_client(
    service: RecordingSubscriptionQueryService,
) -> TestClient:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(
        router,
        prefix="/api/v1",
    )
    application.dependency_overrides[get_database_session] = _database_session
    application.dependency_overrides[get_billing_read_tenant_context] = lambda: object()
    application.dependency_overrides[get_billing_subscription_query_service] = lambda: service

    return TestClient(application)


def test_get_subscription_returns_persisted_public_contract() -> None:
    service = RecordingSubscriptionQueryService()
    client = _build_client(service)

    response = client.get(f"/api/v1/tenants/{TENANT_ID}/billing/subscription")

    assert response.status_code == 200
    assert service.query is not None
    assert service.query.tenant_id == TENANT_ID

    payload = response.json()

    assert payload["tenant_id"] == str(TENANT_ID)
    assert payload["price_code"] == "starter_monthly"
    assert payload["plan"] == "starter"
    assert payload["billing_interval"] == "monthly"
    assert payload["status"] == "active"
    assert payload["unit_amount"] == 4900
    assert "provider_customer_id" not in payload
    assert "provider_subscription_id" not in payload
    assert "provider_reference" not in payload


def test_get_subscription_does_not_require_idempotency_key() -> None:
    client = _build_client(RecordingSubscriptionQueryService())

    response = client.get(f"/api/v1/tenants/{TENANT_ID}/billing/subscription")

    assert response.status_code == 200


def test_missing_subscription_returns_not_found_problem_details() -> None:
    client = _build_client(
        RecordingSubscriptionQueryService(
            error=BillingSubscriptionNotFoundError(),
        )
    )

    response = client.get(f"/api/v1/tenants/{TENANT_ID}/billing/subscription")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")

    payload = response.json()

    assert payload["status"] == 404
    assert payload["code"] == ("billing_subscription_not_found")
