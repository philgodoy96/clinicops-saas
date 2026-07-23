from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from clinicops.api.dependencies import (
    get_database_session,
)
from clinicops.api.errors import (
    PROBLEM_MEDIA_TYPE,
    register_exception_handlers,
)
from clinicops.api.middleware.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)
from clinicops.api.v1.billing.dependencies import (
    get_billing_manage_tenant_context,
    get_create_billing_subscription_service,
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
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionCommand,
    CreatedBillingSubscription,
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


class RecordingSession:
    """Track route-owned billing subscription commits."""

    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


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


@dataclass(slots=True)
class RouteHarness:
    """Isolated billing subscription route state."""

    client: TestClient
    session: RecordingSession
    context: AuthorizedTenantContext
    service: RecordingSubscriptionService


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
    service: RecordingSubscriptionService,
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
    application.dependency_overrides[get_create_billing_subscription_service] = lambda: service

    return RouteHarness(
        client=TestClient(application),
        session=session,
        context=context,
        service=service,
    )


def test_first_execution_returns_created() -> None:
    harness = _build_harness(RecordingSubscriptionService(replayed=False))
    request_id = str(uuid4())
    correlation_id = str(uuid4())

    response = harness.client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        headers={
            "Idempotency-Key": " request-key-123 ",
            REQUEST_ID_HEADER: request_id,
            CORRELATION_ID_HEADER: correlation_id,
        },
        json={
            "price_code": "starter_monthly",
        },
    )

    assert response.status_code == 201
    assert harness.service.command is not None
    assert harness.service.command.tenant_id == TENANT_ID
    assert harness.service.command.idempotency_key == "request-key-123"
    assert harness.service.command.audit_context.actor.user_id == harness.context.user_id
    assert harness.service.command.audit_context.actor.role == harness.context.role.value
    assert harness.service.command.audit_context.source is AuditSource.HTTP
    assert harness.service.command.audit_context.request_id == request_id
    assert harness.service.command.audit_context.correlation_id == correlation_id
    assert harness.session.commit_count == 1
    assert response.json()["price_code"] == ("starter_monthly")
    assert "provider_subscription_id" not in response.json()


def test_successful_replay_returns_ok() -> None:
    harness = _build_harness(RecordingSubscriptionService(replayed=True))
    request_id = str(uuid4())
    correlation_id = str(uuid4())

    response = harness.client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        headers={
            "Idempotency-Key": "request-key-123",
            REQUEST_ID_HEADER: request_id,
            CORRELATION_ID_HEADER: correlation_id,
        },
        json={
            "price_code": "starter_monthly",
        },
    )

    assert response.status_code == 200
    assert harness.service.command is not None
    assert harness.service.command.audit_context.actor.user_id == harness.context.user_id
    assert harness.service.command.audit_context.source is AuditSource.HTTP
    assert harness.service.command.audit_context.request_id == request_id
    assert harness.service.command.audit_context.correlation_id == correlation_id
    assert harness.session.commit_count == 1


def test_missing_idempotency_key_returns_problem_details() -> None:
    harness = _build_harness(RecordingSubscriptionService(replayed=False))

    response = harness.client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        json={
            "price_code": "starter_monthly",
        },
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 400
    assert harness.service.command is None
    assert harness.session.commit_count == 0


def test_request_rejects_client_controlled_billing_fields() -> None:
    harness = _build_harness(RecordingSubscriptionService(replayed=False))

    response = harness.client.post(
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
    assert harness.service.command is None
    assert harness.session.commit_count == 0


def test_create_subscription_rejects_body_trace_fields() -> None:
    harness = _build_harness(RecordingSubscriptionService(replayed=False))

    response = harness.client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        headers={
            "Idempotency-Key": "request-key-123",
        },
        json={
            "price_code": "starter_monthly",
            "request_id": str(uuid4()),
            "correlation_id": str(uuid4()),
            "actor_user_id": str(uuid4()),
        },
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["code"] == "request_validation_error"
    assert harness.service.command is None
    assert harness.session.commit_count == 0
