import pytest
from pydantic import ValidationError

from clinicops.api.v1.billing.schemas import (
    ScheduleBillingPlanChangeRequest,
)


def test_plan_change_request_accepts_target_price_code() -> None:
    request = ScheduleBillingPlanChangeRequest(
        price_code="professional_monthly",
    )

    assert request.price_code == "professional_monthly"


def test_plan_change_request_normalizes_surrounding_whitespace() -> None:
    request = ScheduleBillingPlanChangeRequest(
        price_code="  professional_monthly  ",
    )

    assert request.price_code == "professional_monthly"


def test_plan_change_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ScheduleBillingPlanChangeRequest.model_validate(
            {
                "price_code": "professional_monthly",
                "provider_subscription_id": "fake_sub_forbidden",
            }
        )


def test_plan_change_request_is_immutable() -> None:
    request = ScheduleBillingPlanChangeRequest(
        price_code="professional_monthly",
    )

    with pytest.raises(ValidationError):
        request.price_code = "starter_yearly"
