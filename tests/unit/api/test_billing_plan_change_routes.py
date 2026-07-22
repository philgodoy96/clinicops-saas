from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from clinicops.api.dependencies import (
    get_database_session,
)
from clinicops.api.errors import register_exception_handlers
from clinicops.api.v1.billing.dependencies import (
    get_billing_manage_tenant_context,
    get_schedule_billing_plan_change_service,
)
from clinicops.api.v1.billing.routes import router
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingPlanChangeAlreadyPendingError,
    BillingPlanChangeSamePriceError,
    BillingSubscriptionCancellationPendingError,
    BillingSubscriptionNotActiveError,
)
from clinicops.billing.services.schedule_plan_change import (
    ScheduleBillingPlanChangeCommand,
    ScheduledBillingPlanChange,
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


class RecordingPlanChangeService:
    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self._error = error
        self.command: ScheduleBillingPlanChangeCommand | None = None

    def execute(
        self,
        session: Session,
        command: ScheduleBillingPlanChangeCommand,
    ) -> ScheduledBillingPlanChange:
        self.command = command

        if self._error is not None:
            raise self._error

        return ScheduledBillingPlanChange(
            id=uuid4(),
            tenant_id=command.tenant_id,
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
            pending_price_code=(command.target_price_code),
            created_at=PERIOD_START,
            updated_at=PERIOD_START,
            replayed=False,
        )


def _database_session() -> Iterator[Session]:
    yield cast(Session, object())


def _build_client(
    service: RecordingPlanChangeService,
) -> TestClient:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(
        router,
        prefix="/api/v1",
    )
    application.dependency_overrides[get_database_session] = _database_session
    application.dependency_overrides[get_billing_manage_tenant_context] = lambda: object()
    application.dependency_overrides[get_schedule_billing_plan_change_service] = lambda: service

    return TestClient(application)


def test_plan_change_returns_updated_public_subscription() -> None:
    service = RecordingPlanChangeService()
    client = _build_client(service)

    response = client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/plan-change"),
        headers={
            "Idempotency-Key": "plan-change-request-1",
        },
        json={
            "price_code": "professional_monthly",
        },
    )

    assert response.status_code == 200
    assert service.command is not None
    assert service.command.tenant_id == TENANT_ID
    assert service.command.target_price_code == "professional_monthly"
    assert service.command.idempotency_key == "plan-change-request-1"

    payload = response.json()

    assert payload["tenant_id"] == str(TENANT_ID)
    assert payload["price_code"] == "starter_monthly"
    assert payload["plan"] == "starter"
    assert payload["pending_price_code"] == "professional_monthly"
    assert "replayed" not in payload
    assert "provider_subscription_id" not in payload
    assert "provider_reference" not in payload


def test_plan_change_requires_idempotency_key() -> None:
    client = _build_client(RecordingPlanChangeService())

    response = client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/plan-change"),
        json={
            "price_code": "professional_monthly",
        },
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == ("missing_idempotency_key")


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            BillingPlanChangeSamePriceError(),
            "billing_plan_change_same_price",
        ),
        (
            BillingPlanChangeAlreadyPendingError(),
            "billing_plan_change_already_pending",
        ),
        (
            BillingSubscriptionNotActiveError(),
            "billing_subscription_not_active",
        ),
        (
            BillingSubscriptionCancellationPendingError(),
            "billing_subscription_cancellation_pending",
        ),
    ],
)
def test_plan_change_business_conflicts_return_problem_details(
    error: Exception,
    expected_code: str,
) -> None:
    client = _build_client(RecordingPlanChangeService(error=error))

    response = client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/plan-change"),
        headers={
            "Idempotency-Key": "plan-change-request-1",
        },
        json={
            "price_code": "professional_monthly",
        },
    )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == expected_code
