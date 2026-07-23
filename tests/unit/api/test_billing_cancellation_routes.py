from collections.abc import Iterator
from dataclasses import dataclass
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
from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from clinicops.api.v1.billing.dependencies import (
    get_billing_manage_tenant_context,
    get_schedule_billing_subscription_cancellation_service,
)
from clinicops.api.v1.billing.routes import router
from clinicops.audit.enums import AuditSource
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
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
from clinicops.tenancy.models import TenantRole

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


class RecordingSession:
    """Track route-owned billing cancellation commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


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


@dataclass(slots=True)
class RouteHarness:
    """Isolated billing cancellation route state."""

    client: TestClient
    session: RecordingSession
    context: AuthorizedTenantContext
    service: RecordingCancellationService


def _build_authorized_context(
    tenant_id: UUID,
) -> AuthorizedTenantContext:
    return AuthorizedTenantContext(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid4(),
        role=TenantRole.OWNER,
        granted_permission=TenantPermission.BILLING_MANAGE,
    )


def _build_harness(
    service: RecordingCancellationService,
) -> RouteHarness:
    session = RecordingSession()
    context = _build_authorized_context(TENANT_ID)
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)
    application.include_router(
        router,
        prefix="/api/v1",
    )

    def _database_session() -> Iterator[Session]:
        yield cast(Session, session)

    application.dependency_overrides[get_database_session] = _database_session
    application.dependency_overrides[get_billing_manage_tenant_context] = lambda: context
    application.dependency_overrides[get_schedule_billing_subscription_cancellation_service] = (
        lambda: service
    )

    return RouteHarness(
        client=TestClient(application),
        session=session,
        context=context,
        service=service,
    )


def test_cancellation_returns_updated_public_subscription() -> None:
    harness = _build_harness(RecordingCancellationService())
    request_id = str(uuid4())
    correlation_id = str(uuid4())

    response = harness.client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation"),
        headers={
            "Idempotency-Key": "cancellation-request-1",
            REQUEST_ID_HEADER: request_id,
            CORRELATION_ID_HEADER: correlation_id,
        },
    )

    assert response.status_code == 200
    assert harness.service.command is not None
    assert harness.service.command.tenant_id == TENANT_ID
    assert harness.service.command.idempotency_key == "cancellation-request-1"
    assert harness.service.command.audit_context.actor.user_id == harness.context.user_id
    assert harness.service.command.audit_context.actor.role == harness.context.role.value
    assert harness.service.command.audit_context.source is AuditSource.HTTP
    assert harness.service.command.audit_context.request_id == request_id
    assert harness.service.command.audit_context.correlation_id == correlation_id
    assert harness.session.commit_count == 1

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
    harness = _build_harness(RecordingCancellationService())
    request_id = str(uuid4())
    correlation_id = str(uuid4())

    response = harness.client.post(
        (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation"),
        headers={
            "Idempotency-Key": "bodyless-cancellation",
            REQUEST_ID_HEADER: request_id,
            CORRELATION_ID_HEADER: correlation_id,
        },
    )

    assert response.status_code == 200
    assert harness.service.command is not None
    assert harness.service.command.audit_context.request_id == request_id
    assert harness.session.commit_count == 1


def test_cancellation_requires_idempotency_key() -> None:
    harness = _build_harness(RecordingCancellationService())

    response = harness.client.post(f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation")

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == ("missing_idempotency_key")
    assert harness.service.command is None
    assert harness.session.commit_count == 0


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
    harness = _build_harness(RecordingCancellationService(error=error))

    with TestClient(
        harness.client.app,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation"),
            headers={
                "Idempotency-Key": "cancellation-request-1",
                REQUEST_ID_HEADER: str(uuid4()),
                CORRELATION_ID_HEADER: str(uuid4()),
            },
        )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == expected_code
    assert harness.service.command is not None
    assert harness.session.commit_count == 0


def test_missing_subscription_returns_not_found_problem_details() -> None:
    harness = _build_harness(
        RecordingCancellationService(
            error=BillingSubscriptionNotFoundError(),
        )
    )

    with TestClient(
        harness.client.app,
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            (f"/api/v1/tenants/{TENANT_ID}/billing/subscription/cancellation"),
            headers={
                "Idempotency-Key": "cancellation-request-1",
                REQUEST_ID_HEADER: str(uuid4()),
                CORRELATION_ID_HEADER: str(uuid4()),
            },
        )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == ("billing_subscription_not_found")
    assert harness.service.command is not None
    assert harness.session.commit_count == 0
