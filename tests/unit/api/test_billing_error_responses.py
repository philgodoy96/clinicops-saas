from collections.abc import Iterator
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
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
    get_create_billing_subscription_service,
)
from clinicops.api.v1.billing.routes import router
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.billing.enums import (
    BillingProvider,
    ProviderOperationType,
)
from clinicops.billing.exceptions import (
    BillingIdempotencyConflictError,
    BillingSubscriptionAlreadyExistsError,
    ProviderOperationInProgressError,
)
from clinicops.billing.providers.exceptions import (
    ProviderAmbiguousOutcomeError,
    ProviderRetryableError,
    ProviderTerminalError,
)
from clinicops.tenancy.models import TenantRole

TENANT_ID = UUID("5c9d9f0b-bffd-4519-85e4-817f365daee8")


class RaisingSubscriptionService:
    def __init__(
        self,
        error: Exception,
    ) -> None:
        self._error = error

    def execute(
        self,
        session: Session,
        command: object,
    ) -> object:
        raise self._error


def _database_session() -> Iterator[Session]:
    yield cast(Session, object())


def _tenant_context() -> AuthorizedTenantContext:
    return AuthorizedTenantContext(
        user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=TENANT_ID,
        membership_id=uuid4(),
        role=TenantRole.OWNER,
        granted_permission=TenantPermission.BILLING_MANAGE,
    )


def _request_with_error(
    error: Exception,
) -> Response:
    application = FastAPI()
    application.add_middleware(RequestContextMiddleware)
    register_exception_handlers(application)
    application.include_router(
        router,
        prefix="/api/v1",
    )
    application.dependency_overrides[get_database_session] = _database_session
    application.dependency_overrides[get_billing_manage_tenant_context] = _tenant_context
    application.dependency_overrides[get_create_billing_subscription_service] = lambda: (
        RaisingSubscriptionService(error)
    )

    client = TestClient(application)

    return client.post(
        f"/api/v1/tenants/{TENANT_ID}/billing/subscription",
        headers={
            "Idempotency-Key": "request-key-123",
            REQUEST_ID_HEADER: str(uuid4()),
            CORRELATION_ID_HEADER: str(uuid4()),
        },
        json={
            "price_code": "starter_monthly",
        },
    )


@pytest.mark.parametrize(
    "error",
    [
        BillingSubscriptionAlreadyExistsError(),
        BillingIdempotencyConflictError(),
        ProviderOperationInProgressError(),
        ProviderTerminalError(
            provider=BillingProvider.FAKE,
            operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
            internal_message="Terminal provider rejection.",
        ),
    ],
)
def test_billing_conflicts_return_problem_details(
    error: Exception,
) -> None:
    response = _request_with_error(error)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 409


@pytest.mark.parametrize(
    "error",
    [
        ProviderRetryableError(
            provider=BillingProvider.FAKE,
            operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
            internal_message="Temporary provider failure.",
        ),
        ProviderAmbiguousOutcomeError(
            provider=BillingProvider.FAKE,
            operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
            internal_message="Provider result is ambiguous.",
        ),
    ],
)
def test_retryable_provider_errors_return_service_unavailable(
    error: Exception,
) -> None:
    response = _request_with_error(error)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 503
