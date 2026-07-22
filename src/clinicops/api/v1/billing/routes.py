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
)
from clinicops.api.v1.billing.schemas import (
    BillingSubscriptionResponse,
    CreateBillingSubscriptionRequest,
    ScheduleBillingPlanChangeRequest,
)
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionCommand,
    CreatedBillingSubscription,
)
from clinicops.billing.services.get_subscription import (
    BillingSubscriptionDetails,
    GetBillingSubscriptionQuery,
)
from clinicops.billing.services.schedule_plan_change import (
    ScheduleBillingPlanChangeCommand,
    ScheduledBillingPlanChange,
)

router = APIRouter(
    prefix="/tenants",
    tags=["billing"],
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
    _tenant_context: BillingManageTenantContextDependency,
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
        ),
    )

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
    _tenant_context: BillingManageTenantContextDependency,
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
        ),
    )

    return _to_response(result)


def _to_response(
    result: (BillingSubscriptionDetails | CreatedBillingSubscription | ScheduledBillingPlanChange),
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
