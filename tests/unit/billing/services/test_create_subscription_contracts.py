from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from clinicops.audit.context import AuditRecordingContext
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingError,
    BillingIdempotencyConflictError,
    MissingIdempotencyKeyError,
    ProviderOperationInProgressError,
    UnsupportedPriceCodeError,
)
from clinicops.billing.services.create_subscription import (
    CreateBillingSubscriptionCommand,
    CreatedBillingSubscription,
)
from clinicops.tenancy.models import TenantRole


def _audit_context() -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=uuid4(),
        role=TenantRole.OWNER.value,
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
    )


def _created_subscription(
    **overrides: object,
) -> CreatedBillingSubscription:
    values: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "price_code": "starter_monthly",
        "plan": BillingPlan.STARTER,
        "billing_interval": BillingInterval.MONTHLY,
        "currency": "USD",
        "unit_amount": 4900,
        "status": SubscriptionStatus.ACTIVE,
        "current_period_start": datetime(
            2026,
            7,
            21,
            12,
            tzinfo=UTC,
        ),
        "current_period_end": datetime(
            2026,
            8,
            21,
            12,
            tzinfo=UTC,
        ),
        "cancel_at_period_end": False,
        "cancellation_requested_at": None,
        "canceled_at": None,
        "pending_price_code": None,
        "created_at": datetime(
            2026,
            7,
            21,
            12,
            tzinfo=UTC,
        ),
        "updated_at": datetime(
            2026,
            7,
            21,
            12,
            tzinfo=UTC,
        ),
        "replayed": False,
    }
    values.update(overrides)

    return CreatedBillingSubscription(
        **values,  # type: ignore[arg-type]
    )


def test_command_normalizes_price_code_and_idempotency_key() -> None:
    command = CreateBillingSubscriptionCommand(
        tenant_id=uuid4(),
        price_code=" starter_monthly ",
        idempotency_key=" request-key-123 ",
        audit_context=_audit_context(),
    )

    assert command.price_code == "starter_monthly"
    assert command.idempotency_key == "request-key-123"


def test_command_rejects_unsupported_price_code() -> None:
    with pytest.raises(UnsupportedPriceCodeError):
        CreateBillingSubscriptionCommand(
            tenant_id=uuid4(),
            price_code="unsupported_price",
            idempotency_key="request-key-123",
            audit_context=_audit_context(),
        )


def test_command_requires_immutable_audit_context() -> None:
    context = _audit_context()
    command = CreateBillingSubscriptionCommand(
        tenant_id=uuid4(),
        price_code="starter_monthly",
        idempotency_key="request-key-123",
        audit_context=context,
    )

    assert command.audit_context is context

    with pytest.raises(FrozenInstanceError):
        command.audit_context = _audit_context()  # type: ignore[misc]


def test_command_is_immutable() -> None:
    command = CreateBillingSubscriptionCommand(
        tenant_id=uuid4(),
        price_code="starter_monthly",
        idempotency_key="request-key-123",
        audit_context=_audit_context(),
    )

    with pytest.raises(FrozenInstanceError):
        command.price_code = "professional_monthly"  # type: ignore[misc]


def test_result_normalizes_datetimes_and_currency() -> None:
    source_timezone = timezone(-timedelta(hours=3))
    result = _created_subscription(
        currency=" usd ",
        current_period_start=datetime(
            2026,
            7,
            21,
            9,
            tzinfo=source_timezone,
        ),
        current_period_end=datetime(
            2026,
            8,
            21,
            9,
            tzinfo=source_timezone,
        ),
    )

    assert result.currency == "USD"
    assert result.current_period_start == datetime(
        2026,
        7,
        21,
        12,
        tzinfo=UTC,
    )
    assert result.current_period_end == datetime(
        2026,
        8,
        21,
        12,
        tzinfo=UTC,
    )


def test_result_rejects_unordered_period() -> None:
    boundary = datetime(
        2026,
        7,
        21,
        12,
        tzinfo=UTC,
    )

    with pytest.raises(
        ValueError,
        match="must be earlier",
    ):
        _created_subscription(
            current_period_start=boundary,
            current_period_end=boundary,
        )


def test_result_rejects_nonpositive_unit_amount() -> None:
    with pytest.raises(
        ValueError,
        match="greater than zero",
    ):
        _created_subscription(unit_amount=0)


@pytest.mark.parametrize(
    (
        "error",
        "expected_code",
    ),
    [
        (
            MissingIdempotencyKeyError(),
            "missing_idempotency_key",
        ),
        (
            BillingIdempotencyConflictError(),
            "billing_idempotency_conflict",
        ),
        (
            ProviderOperationInProgressError(),
            "provider_operation_in_progress",
        ),
    ],
)
def test_new_billing_errors_have_stable_codes(
    error: BillingError,
    expected_code: str,
) -> None:
    assert error.code == expected_code
