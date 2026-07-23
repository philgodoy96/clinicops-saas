from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from clinicops.audit.context import AuditRecordingContext
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingError,
    BillingPlanChangeAlreadyPendingError,
    BillingPlanChangeSamePriceError,
    BillingSubscriptionCancellationPendingError,
    BillingSubscriptionNotActiveError,
)
from clinicops.billing.services.schedule_plan_change import (
    ScheduleBillingPlanChangeCommand,
    ScheduledBillingPlanChange,
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


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=uuid4(),
        role=TenantRole.OWNER.value,
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
    )


def test_schedule_plan_change_command_is_immutable() -> None:
    command = ScheduleBillingPlanChangeCommand(
        tenant_id=TENANT_ID,
        target_price_code="professional_monthly",
        idempotency_key="plan-change-request-1",
        audit_context=_audit_context(),
    )

    with pytest.raises(FrozenInstanceError):
        command.target_price_code = (  # type: ignore[misc]
            "starter_yearly"
        )


def test_schedule_plan_change_command_requires_immutable_audit_context() -> None:
    context = _audit_context()
    command = ScheduleBillingPlanChangeCommand(
        tenant_id=TENANT_ID,
        target_price_code="professional_monthly",
        idempotency_key="plan-change-request-1",
        audit_context=context,
    )

    assert command.audit_context is context

    with pytest.raises(FrozenInstanceError):
        command.audit_context = _audit_context()  # type: ignore[misc]


def test_scheduled_plan_change_exposes_public_subscription_state() -> None:
    result = ScheduledBillingPlanChange(
        id=uuid4(),
        tenant_id=TENANT_ID,
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
        pending_price_code="professional_monthly",
        created_at=PERIOD_START,
        updated_at=PERIOD_START,
        replayed=False,
    )

    assert result.price_code == "starter_monthly"
    assert result.pending_price_code == ("professional_monthly")
    assert result.replayed is False
    assert not hasattr(
        result,
        "provider_subscription_id",
    )
    assert not hasattr(
        result,
        "provider_reference",
    )


@pytest.mark.parametrize(
    ("error_type", "expected_code"),
    [
        (
            BillingPlanChangeSamePriceError,
            "billing_plan_change_same_price",
        ),
        (
            BillingPlanChangeAlreadyPendingError,
            "billing_plan_change_already_pending",
        ),
        (
            BillingSubscriptionNotActiveError,
            "billing_subscription_not_active",
        ),
        (
            BillingSubscriptionCancellationPendingError,
            "billing_subscription_cancellation_pending",
        ),
    ],
)
def test_plan_change_errors_have_stable_codes(
    error_type: type[BillingError],
    expected_code: str,
) -> None:
    error = error_type()

    assert error.code == expected_code
    assert isinstance(
        error.public_message,
        str,
    )
