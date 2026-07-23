from typing import Annotated, cast

from fastapi import Depends, Header, Request

from clinicops.api.v1.tenants.dependencies import require_tenant_permission
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import AuthorizedTenantContext
from clinicops.billing.exceptions import (
    MissingIdempotencyKeyError,
)
from clinicops.billing.idempotency_keys import (
    validate_idempotency_key,
)
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionService,
)
from clinicops.billing.services.get_subscription import (
    GetBillingSubscriptionService,
)
from clinicops.billing.services.schedule_cancellation import (
    ScheduleBillingSubscriptionCancellationService,
)
from clinicops.billing.services.schedule_plan_change import (
    ScheduleBillingPlanChangeService,
)
from clinicops.billing.webhooks.enqueue_processing_job import (
    EnqueueBillingWebhookProcessingJobService,
)

IdempotencyKeyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key"),
]


def get_billing_idempotency_key(
    idempotency_key: IdempotencyKeyHeader = None,
) -> str:
    """Return the validated client billing idempotency key."""

    if idempotency_key is None:
        raise MissingIdempotencyKeyError()

    return validate_idempotency_key(idempotency_key)


def get_payment_provider(
    request: Request,
) -> PaymentProvider:
    """Return the application-scoped payment provider."""

    payment_provider = getattr(
        request.app.state,
        "payment_provider",
        None,
    )

    if payment_provider is None:
        raise RuntimeError("The payment provider is not configured.")

    return cast(PaymentProvider, payment_provider)


def get_create_billing_subscription_service(
    payment_provider: Annotated[
        PaymentProvider,
        Depends(get_payment_provider),
    ],
) -> CreateBillingSubscriptionService:
    """Build the subscription-creation orchestrator."""

    return CreateBillingSubscriptionService(
        payment_provider=payment_provider,
    )


def get_billing_subscription_query_service() -> GetBillingSubscriptionService:
    """Build the tenant billing subscription query service."""

    return GetBillingSubscriptionService()


def get_schedule_billing_plan_change_service(
    payment_provider: Annotated[
        PaymentProvider,
        Depends(get_payment_provider),
    ],
) -> ScheduleBillingPlanChangeService:
    """Build the scheduled plan-change orchestrator."""

    return ScheduleBillingPlanChangeService(
        payment_provider=payment_provider,
    )


def get_schedule_billing_subscription_cancellation_service(
    payment_provider: Annotated[
        PaymentProvider,
        Depends(get_payment_provider),
    ],
) -> ScheduleBillingSubscriptionCancellationService:
    """Build the scheduled subscription-cancellation orchestrator."""

    return ScheduleBillingSubscriptionCancellationService(
        payment_provider=payment_provider,
    )


def get_enqueue_billing_webhook_processing_job_service() -> (
    EnqueueBillingWebhookProcessingJobService
):
    """Build the billing webhook processing-job enqueue service."""

    return EnqueueBillingWebhookProcessingJobService()


get_billing_read_tenant_context = require_tenant_permission(TenantPermission.BILLING_READ)
get_billing_manage_tenant_context = require_tenant_permission(TenantPermission.BILLING_MANAGE)


BillingIdempotencyKeyDependency = Annotated[
    str,
    Depends(get_billing_idempotency_key),
]
BillingReadTenantContextDependency = Annotated[
    AuthorizedTenantContext,
    Depends(get_billing_read_tenant_context),
]
BillingManageTenantContextDependency = Annotated[
    AuthorizedTenantContext,
    Depends(get_billing_manage_tenant_context),
]
CreateBillingSubscriptionServiceDependency = Annotated[
    CreateBillingSubscriptionService,
    Depends(get_create_billing_subscription_service),
]
GetBillingSubscriptionServiceDependency = Annotated[
    GetBillingSubscriptionService,
    Depends(get_billing_subscription_query_service),
]
ScheduleBillingPlanChangeServiceDependency = Annotated[
    ScheduleBillingPlanChangeService,
    Depends(get_schedule_billing_plan_change_service),
]
ScheduleBillingSubscriptionCancellationServiceDependency = Annotated[
    ScheduleBillingSubscriptionCancellationService,
    Depends(get_schedule_billing_subscription_cancellation_service),
]
EnqueueBillingWebhookProcessingJobServiceDependency = Annotated[
    EnqueueBillingWebhookProcessingJobService,
    Depends(get_enqueue_billing_webhook_processing_job_service),
]
