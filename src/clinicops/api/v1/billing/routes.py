from uuid import UUID

from fastapi import APIRouter, Response, status

from clinicops.api.dependencies import (
    DatabaseSessionDependency,
)
from clinicops.api.v1.billing.dependencies import (
    BillingIdempotencyKeyDependency,
    BillingManageTenantContextDependency,
    BillingReadTenantContextDependency,
    CreateBillingSubscriptionServiceDependency,
    GetBillingSubscriptionServiceDependency,
    ScheduleBillingPlanChangeServiceDependency,
    ScheduleBillingSubscriptionCancellationServiceDependency,
)
from clinicops.api.v1.billing.schemas import (
    BillingSubscriptionResponse,
    CreateBillingSubscriptionRequest,
    ScheduleBillingPlanChangeRequest,
)
from clinicops.audit.context import AuditRecordingContext
from clinicops.authorization.services.require_permission import (
    AuthorizedTenantContext,
)
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionCommand,
    CreatedBillingSubscription,
)
from clinicops.billing.services.get_subscription import (
    BillingSubscriptionDetails,
    GetBillingSubscriptionQuery,
)
from clinicops.billing.services.schedule_cancellation import (
    ScheduleBillingSubscriptionCancellationCommand,
    ScheduledBillingSubscriptionCancellation,
)
from clinicops.billing.services.schedule_plan_change import (
    ScheduleBillingPlanChangeCommand,
    ScheduledBillingPlanChange,
)
from clinicops.core.request_context import (
    get_correlation_id,
    get_request_id,
)

router = APIRouter(
    prefix="/tenants",
    tags=["billing"],
)


def _http_audit_context(
    tenant_context: AuthorizedTenantContext,
) -> AuditRecordingContext:
    """Build audit attribution from trusted runtime request state."""

    request_id = get_request_id()
    correlation_id = get_correlation_id()

    if request_id is None or correlation_id is None:
        raise RuntimeError(
            "Request context identifiers are required for audit recording.",
        )

    return AuditRecordingContext.http_user(
        user_id=tenant_context.user_id,
        role=tenant_context.role.value,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.get(
    "/{tenant_id}/billing/subscription",
    response_model=BillingSubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get tenant billing subscription",
)
def get_billing_subscription(
    tenant_id: UUID,
    session: DatabaseSessionDependency,
    _tenant_context: BillingReadTenantContextDependency,
    service: GetBillingSubscriptionServiceDependency,
) -> BillingSubscriptionResponse:
    """Return the current persisted tenant billing subscription."""

    result = service.execute(
        session,
        GetBillingSubscriptionQuery(
            tenant_id=tenant_id,
        ),
    )

    return _to_response(result)


@router.post(
    "/{tenant_id}/billing/subscription",
    response_model=BillingSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create tenant billing subscription",
)
def create_billing_subscription(
    tenant_id: UUID,
    payload: CreateBillingSubscriptionRequest,
    response: Response,
    session: DatabaseSessionDependency,
    tenant_context: BillingManageTenantContextDependency,
    idempotency_key: BillingIdempotencyKeyDependency,
    service: CreateBillingSubscriptionServiceDependency,
) -> BillingSubscriptionResponse:
    """Create or replay the tenant's billing subscription."""

    result = service.execute(
        session,
        CreateBillingSubscriptionCommand(
            tenant_id=tenant_id,
            price_code=payload.price_code,
            idempotency_key=idempotency_key,
            audit_context=_http_audit_context(tenant_context),
        ),
    )
    session.commit()

    response.status_code = status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED

    return _to_response(result)


@router.post(
    "/{tenant_id}/billing/subscription/plan-change",
    response_model=BillingSubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Schedule tenant billing plan change",
)
def schedule_billing_plan_change(
    tenant_id: UUID,
    payload: ScheduleBillingPlanChangeRequest,
    session: DatabaseSessionDependency,
    tenant_context: BillingManageTenantContextDependency,
    idempotency_key: BillingIdempotencyKeyDependency,
    service: ScheduleBillingPlanChangeServiceDependency,
) -> BillingSubscriptionResponse:
    """Schedule or replay a period-end billing plan change."""

    result = service.execute(
        session,
        ScheduleBillingPlanChangeCommand(
            tenant_id=tenant_id,
            target_price_code=payload.price_code,
            idempotency_key=idempotency_key,
            audit_context=_http_audit_context(tenant_context),
        ),
    )
    session.commit()

    return _to_response(result)


@router.post(
    "/{tenant_id}/billing/subscription/cancellation",
    response_model=BillingSubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Schedule tenant billing subscription cancellation",
)
def schedule_billing_subscription_cancellation(
    tenant_id: UUID,
    session: DatabaseSessionDependency,
    tenant_context: BillingManageTenantContextDependency,
    idempotency_key: BillingIdempotencyKeyDependency,
    service: (ScheduleBillingSubscriptionCancellationServiceDependency),
) -> BillingSubscriptionResponse:
    """Schedule or replay a period-end subscription cancellation."""

    result = service.execute(
        session,
        ScheduleBillingSubscriptionCancellationCommand(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            audit_context=_http_audit_context(tenant_context),
        ),
    )
    session.commit()

    return _to_response(result)


def _to_response(
    result: (
        BillingSubscriptionDetails
        | CreatedBillingSubscription
        | ScheduledBillingPlanChange
        | ScheduledBillingSubscriptionCancellation
    ),
) -> BillingSubscriptionResponse:
    return BillingSubscriptionResponse(
        id=result.id,
        tenant_id=result.tenant_id,
        price_code=result.price_code,
        plan=result.plan,
        billing_interval=result.billing_interval,
        currency=result.currency,
        unit_amount=result.unit_amount,
        status=result.status,
        current_period_start=result.current_period_start,
        current_period_end=result.current_period_end,
        cancel_at_period_end=result.cancel_at_period_end,
        cancellation_requested_at=(result.cancellation_requested_at),
        canceled_at=result.canceled_at,
        pending_price_code=result.pending_price_code,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )
