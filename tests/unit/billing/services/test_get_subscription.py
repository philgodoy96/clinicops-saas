from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingSubscriptionNotFoundError,
)
from clinicops.billing.models import Subscription
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
)
from clinicops.billing.services.get_subscription import (
    GetBillingSubscriptionQuery,
    GetBillingSubscriptionService,
)

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


class RecordingSubscriptionRepository:
    def __init__(
        self,
        subscription: Subscription | None,
    ) -> None:
        self.subscription = subscription
        self.tenant_id: UUID | None = None
        self.read_count = 0

    def get_by_tenant_id(
        self,
        session: Session,
        *,
        tenant_id: UUID,
    ) -> Subscription | None:
        self.tenant_id = tenant_id
        self.read_count += 1
        return self.subscription

    def get_by_tenant_id_for_update(
        self,
        session: Session,
        *,
        tenant_id: UUID,
    ) -> Subscription | None:
        raise AssertionError("The read service must not acquire a row lock.")


def _subscription() -> Subscription:
    subscription = Subscription(
        id=uuid4(),
        tenant_id=TENANT_ID,
        billing_customer_id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_subscription_id="fake_sub_subscription",
        price_code="starter_monthly",
        plan=BillingPlan.STARTER,
        billing_interval=BillingInterval.MONTHLY,
        currency="USD",
        unit_amount=4900,
        pending_price_code=None,
        status=SubscriptionStatus.ACTIVE,
        cancel_at_period_end=False,
        cancellation_requested_at=None,
        current_period_start=PERIOD_START,
        current_period_end=PERIOD_END,
        provider_state_version=1,
        last_provider_event_at=None,
        canceled_at=None,
    )
    subscription.created_at = PERIOD_START
    subscription.updated_at = PERIOD_START

    return subscription


def _service(
    repository: RecordingSubscriptionRepository,
) -> GetBillingSubscriptionService:
    return GetBillingSubscriptionService(
        subscription_repository=cast(
            SubscriptionRepository,
            repository,
        )
    )


def test_service_returns_tenant_scoped_subscription_details() -> None:
    subscription = _subscription()
    repository = RecordingSubscriptionRepository(subscription)
    service = _service(repository)

    result = service.execute(
        cast(Session, object()),
        GetBillingSubscriptionQuery(tenant_id=TENANT_ID),
    )

    assert repository.tenant_id == TENANT_ID
    assert repository.read_count == 1
    assert result.id == subscription.id
    assert result.tenant_id == TENANT_ID
    assert result.price_code == "starter_monthly"
    assert result.plan is BillingPlan.STARTER
    assert result.billing_interval is BillingInterval.MONTHLY
    assert result.status is SubscriptionStatus.ACTIVE
    assert result.current_period_start == PERIOD_START
    assert result.current_period_end == PERIOD_END
    assert not hasattr(
        result,
        "provider_subscription_id",
    )
    assert not hasattr(
        result,
        "provider_customer_id",
    )


def test_service_raises_when_subscription_does_not_exist() -> None:
    repository = RecordingSubscriptionRepository(None)
    service = _service(repository)

    with pytest.raises(BillingSubscriptionNotFoundError) as exception_info:
        service.execute(
            cast(Session, object()),
            GetBillingSubscriptionQuery(tenant_id=TENANT_ID),
        )

    assert exception_info.value.code == "billing_subscription_not_found"
    assert repository.tenant_id == TENANT_ID


def test_query_is_immutable() -> None:
    query = GetBillingSubscriptionQuery(tenant_id=TENANT_ID)

    with pytest.raises(FrozenInstanceError):
        query.tenant_id = uuid4()  # type: ignore[misc]
