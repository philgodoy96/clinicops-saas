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
    get_billing_manage_tenant_context,
    get_create_billing_subscription_service,
)
from clinicops.api.v1.billing.routes import router
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionCommand,
    CreatedBillingSubscription,
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


class RecordingSubscriptionService:
    def __init__(
        self,
        *,
        replayed: bool,
    ) -> None:
        self._replayed = replayed
        self.command: CreateBillingSubscriptionCommand | None = None

    def execute(
        self,
        session: Session,
        command: CreateBillingSubscriptionCommand,
    ) -> CreatedBillingSubscription:
        self.command = command

        return CreatedBillingSubscription(
            id=uuid4(),
            tenant_id=command.tenant_id,
            price_code=command.price_code,
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
            replayed=self._replayed,
        )


def _database_session() -> Iterator[Session]:
    yield cast(Session, object())


def _build_client(
    service: RecordingSubscriptionService,
) -> TestClient:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(
        router,
        prefix="/api/v1",
    )
    application.dependency_overrides[get_database_session] = _database_session
    application.dependency_overrides[get_billing_manage_tenant_context] = lambda: object()
    application.dependency_overrides[get_create_billing_subscription_service] = lambda: service

    return TestClient(application)


def test_first_execution_returns_created() -> None:
    service = RecordingSubscriptionService(replayed=False)
    client = _build_client(service)

    response = client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        headers={
            "Idempotency-Key": " request-key-123 ",
        },
        json={
            "price_code": "starter_monthly",
        },
    )

    assert response.status_code == 201
    assert service.command is not None
    assert service.command.tenant_id == TENANT_ID
    assert service.command.idempotency_key == "request-key-123"
    assert response.json()["price_code"] == ("starter_monthly")
    assert "provider_subscription_id" not in response.json()


def test_successful_replay_returns_ok() -> None:
    service = RecordingSubscriptionService(replayed=True)
    client = _build_client(service)

    response = client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        headers={
            "Idempotency-Key": "request-key-123",
        },
        json={
            "price_code": "starter_monthly",
        },
    )

    assert response.status_code == 200


def test_missing_idempotency_key_returns_problem_details() -> None:
    client = _build_client(RecordingSubscriptionService(replayed=False))

    response = client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        json={
            "price_code": "starter_monthly",
        },
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 400


def test_request_rejects_client_controlled_billing_fields() -> None:
    client = _build_client(RecordingSubscriptionService(replayed=False))

    response = client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        headers={
            "Idempotency-Key": "request-key-123",
        },
        json={
            "price_code": "starter_monthly",
            "unit_amount": 1,
        },
    )

    assert response.status_code == 422
