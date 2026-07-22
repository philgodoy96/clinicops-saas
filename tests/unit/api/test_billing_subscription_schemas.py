from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinicops.api.v1.billing.schemas import (
    BillingSubscriptionResponse,
    CreateBillingSubscriptionRequest,
)
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)


def test_create_subscription_request_normalizes_price_code() -> None:
    request = CreateBillingSubscriptionRequest(price_code=" starter_monthly ")

    assert request.price_code == "starter_monthly"


def test_create_subscription_request_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CreateBillingSubscriptionRequest.model_validate(
            {
                "price_code": "starter_monthly",
                "unit_amount": 1,
            }
        )


def test_subscription_response_serializes_public_contract() -> None:
    subscription_id = uuid4()
    tenant_id = uuid4()
    period_start = datetime(
        2026,
        7,
        21,
        12,
        tzinfo=UTC,
    )
    period_end = datetime(
        2026,
        8,
        21,
        12,
        tzinfo=UTC,
    )

    response = BillingSubscriptionResponse(
        id=subscription_id,
        tenant_id=tenant_id,
        price_code="starter_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=4900,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=period_start,
        current_period_end=period_end,
        cancel_at_period_end=False,
        cancellation_requested_at=None,
        canceled_at=None,
        pending_price_code=None,
        created_at=period_start,
        updated_at=period_start,
    )

    payload = response.model_dump(mode="json")

    assert payload["id"] == str(subscription_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["plan"] == "starter"
    assert payload["billing_interval"] == "monthly"
    assert payload["status"] == "active"
    assert "provider_customer_id" not in payload
    assert "provider_subscription_id" not in payload


def test_subscription_response_is_immutable() -> None:
    response = BillingSubscriptionResponse(
        id=uuid4(),
        tenant_id=uuid4(),
        price_code="starter_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=4900,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=datetime(
            2026,
            7,
            21,
            tzinfo=UTC,
        ),
        current_period_end=datetime(
            2026,
            8,
            21,
            tzinfo=UTC,
        ),
        cancel_at_period_end=False,
        cancellation_requested_at=None,
        canceled_at=None,
        pending_price_code=None,
        created_at=datetime(
            2026,
            7,
            21,
            tzinfo=UTC,
        ),
        updated_at=datetime(
            2026,
            7,
            21,
            tzinfo=UTC,
        ),
    )

    with pytest.raises(
        ValidationError,
        match="Instance is frozen",
    ):
        response.price_code = "professional_monthly"
