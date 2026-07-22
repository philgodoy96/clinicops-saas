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
    get_schedule_billing_subscription_cancellation_service,
)
from clinicops.api.v1.billing.routes import router
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingSubscriptionAlreadyCanceledError,
    BillingSubscriptionCancellationPendingError,
    BillingSubscriptionNotActiveError,
    BillingSubscriptionNotFoundError,
)
from clinicops.billing.services.schedule_cancellation import (
    ScheduleBillingSubscriptionCancellationCommand,
    ScheduledBillingSubscriptionCancellation,
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
CANCELLATION_REQUESTED_AT = datetime(
    2026,
    7,
    24,
    15,
    tzinfo=UTC,
)


class RecordingCancellationService:
    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self._error = error
        self.command: ScheduleBillingSubscriptionCancellationCommand | None = None

    def execute(
        self,
        session: Session,
        command: (ScheduleBillingSubscriptionCancellationCommand),
    ) -> ScheduledBillingSubscriptionCancellation:
        self.command = command

        if self._error is not None:
            raise self._error

        return ScheduledBillingSubscriptionCancellation(
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
            cancel_at_period_end=True,
            cancellation_requested_at=(CANCELLATION_REQUESTED_AT),
            canceled_at=None,
            pending_price_code=None,
            created_at=PERIOD_START,
            updated_at=CANCELLATION_REQUESTED_AT,
            replayed=False,
        )


def _database_session() -> Iterator[Session]:
    yield cast(Session, object())


def _build_client(
    service: RecordingCancellationService,
) -> TestClient:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(
        router,
        prefix="/api/v1",
    )
    application.dependency_overrides[get_database_session] = _database_session
    application.dependency_overrides[get_billing_manage_tenant_context] = lambda: object()
    application.dependency_overrides[get_schedule_billing_subscription_cancellation_service] = (
        lambda: service
    )

    return TestClient(application)


def test_cancellation_returns_updated_public_subscription() -> None:
    service = RecordingCancellationService()
    client = _build_client(service)

    response = client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation"),
        headers={
            "Idempotency-Key": "cancellation-request-1",
        },
    )

    assert response.status_code == 200
    assert service.command is not None
    assert service.command.tenant_id == TENANT_ID
    assert service.command.idempotency_key == "cancellation-request-1"

    payload = response.json()

    assert payload["tenant_id"] == str(TENANT_ID)
    assert payload["status"] == "active"
    assert payload["cancel_at_period_end"] is True
    assert payload["cancellation_requested_at"] == ("2026-07-24T15:00:00Z")
    assert payload["canceled_at"] is None
    assert payload["pending_price_code"] is None
    assert "replayed" not in payload
    assert "provider_subscription_id" not in payload
    assert "provider_reference" not in payload


def test_cancellation_does_not_require_request_body() -> None:
    client = _build_client(RecordingCancellationService())

    response = client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation"),
        headers={
            "Idempotency-Key": "bodyless-cancellation",
        },
    )

    assert response.status_code == 200


def test_cancellation_requires_idempotency_key() -> None:
    client = _build_client(RecordingCancellationService())

    response = client.post(f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation")

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == ("missing_idempotency_key")


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            BillingSubscriptionCancellationPendingError(),
            "billing_subscription_cancellation_pending",
        ),
        (
            BillingSubscriptionNotActiveError(),
            "billing_subscription_not_active",
        ),
        (
            BillingSubscriptionAlreadyCanceledError(),
            "billing_subscription_already_canceled",
        ),
    ],
)
def test_cancellation_business_conflicts_return_problem_details(
    error: Exception,
    expected_code: str,
) -> None:
    client = _build_client(RecordingCancellationService(error=error))

    response = client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation"),
        headers={
            "Idempotency-Key": "cancellation-request-1",
        },
    )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == expected_code


def test_missing_subscription_returns_not_found_problem_details() -> None:
    client = _build_client(
        RecordingCancellationService(
            error=BillingSubscriptionNotFoundError(),
        )
    )

    response = client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation"),
        headers={
            "Idempotency-Key": "cancellation-request-1",
        },
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == ("billing_subscription_not_found")
