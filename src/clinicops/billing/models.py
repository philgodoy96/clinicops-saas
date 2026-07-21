from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import (
    ENUM as PostgreSQLEnum,
)
from sqlalchemy.dialects.postgresql import (
    JSONB,
)
from sqlalchemy.dialects.postgresql import (
    UUID as PostgreSQLUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
    WebhookEventStatus,
)
from clinicops.db.base import Base
from clinicops.tenancy.models import Tenant


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [str(member.value) for member in enum_class]


billing_plan_enum = PostgreSQLEnum(
    BillingPlan,
    name="billing_plan",
    values_callable=_enum_values,
)

billing_interval_enum = PostgreSQLEnum(
    BillingInterval,
    name="billing_interval",
    values_callable=_enum_values,
)

subscription_status_enum = PostgreSQLEnum(
    SubscriptionStatus,
    name="subscription_status",
    values_callable=_enum_values,
)

billing_provider_enum = PostgreSQLEnum(
    BillingProvider,
    name="billing_provider",
    values_callable=_enum_values,
)

provider_operation_type_enum = PostgreSQLEnum(
    ProviderOperationType,
    name="provider_operation_type",
    values_callable=_enum_values,
)

provider_operation_status_enum = PostgreSQLEnum(
    ProviderOperationStatus,
    name="provider_operation_status",
    values_callable=_enum_values,
)

webhook_event_status_enum = PostgreSQLEnum(
    WebhookEventStatus,
    name="webhook_event_status",
    values_callable=_enum_values,
)


class BillingCustomer(Base):
    """Tenant-scoped customer identity at a payment provider."""

    __tablename__ = "billing_customers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            name="uq_billing_customers_tenant_id_provider",
        ),
        Index(
            "uq_billing_customers_provider_customer_id",
            "provider",
            "provider_customer_id",
            unique=True,
            postgresql_where=text("provider_customer_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_billing_customers_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    provider: Mapped[BillingProvider] = mapped_column(
        billing_provider_enum,
        nullable=False,
    )
    provider_customer_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    tenant: Mapped[Tenant] = relationship()


class Subscription(Base):
    """Tenant-owned local subscription lifecycle."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            name="uq_subscriptions_tenant_id",
        ),
        Index(
            "uq_subscriptions_provider_subscription_id",
            "provider",
            "provider_subscription_id",
            unique=True,
            postgresql_where=text("provider_subscription_id IS NOT NULL"),
        ),
        Index(
            "ix_subscriptions_billing_customer_id",
            "billing_customer_id",
        ),
        CheckConstraint(
            "unit_amount > 0",
            name="ck_subscriptions_unit_amount_positive",
        ),
        CheckConstraint(
            "provider_state_version >= 0",
            name=("ck_subscriptions_provider_state_version_nonnegative"),
        ),
        CheckConstraint(
            ("pending_price_code IS NULL OR pending_price_code <> price_code"),
            name="ck_subscriptions_pending_price_differs",
        ),
        CheckConstraint(
            (
                "(current_period_start IS NULL "
                "AND current_period_end IS NULL) "
                "OR "
                "(current_period_start IS NOT NULL "
                "AND current_period_end IS NOT NULL "
                "AND current_period_start < current_period_end)"
            ),
            name="ck_subscriptions_period_pair",
        ),
        CheckConstraint(
            (
                "status NOT IN ('active', 'past_due') "
                "OR "
                "(current_period_start IS NOT NULL "
                "AND current_period_end IS NOT NULL)"
            ),
            name="ck_subscriptions_active_period",
        ),
        CheckConstraint(
            (
                "("
                "status = 'canceled' "
                "AND canceled_at IS NOT NULL "
                "AND cancel_at_period_end = false"
                ") "
                "OR "
                "("
                "status <> 'canceled' "
                "AND canceled_at IS NULL "
                "AND ("
                "("
                "cancel_at_period_end = true "
                "AND cancellation_requested_at IS NOT NULL"
                ") "
                "OR "
                "("
                "cancel_at_period_end = false "
                "AND cancellation_requested_at IS NULL"
                ")"
                ")"
                ")"
            ),
            name="ck_subscriptions_cancellation_consistency",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_subscriptions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    billing_customer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "billing_customers.id",
            name=("fk_subscriptions_billing_customer_id_billing_customers"),
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    provider: Mapped[BillingProvider] = mapped_column(
        billing_provider_enum,
        nullable=False,
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    price_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    plan: Mapped[BillingPlan] = mapped_column(
        billing_plan_enum,
        nullable=False,
    )
    billing_interval: Mapped[BillingInterval] = mapped_column(
        billing_interval_enum,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )
    unit_amount: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    pending_price_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        subscription_status_enum,
        nullable=False,
        default=SubscriptionStatus.PENDING,
        server_default=SubscriptionStatus.PENDING.value,
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_state_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_provider_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    tenant: Mapped[Tenant] = relationship()
    billing_customer: Mapped[BillingCustomer] = relationship()


class ProviderOperation(Base):
    """Durable idempotency boundary for an outbound provider mutation."""

    __tablename__ = "provider_operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "operation_type",
            "idempotency_key",
            name="uq_provider_operations_tenant_op_idempotency",
        ),
        Index(
            "ix_provider_operations_tenant_id_status",
            "tenant_id",
            "status",
        ),
        Index(
            "ix_provider_operations_provider_reference",
            "provider_reference",
            postgresql_where=text("provider_reference IS NOT NULL"),
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_provider_operations_attempt_count_nonnegative",
        ),
        CheckConstraint(
            ("request_fingerprint ~ '^[0-9a-f]{64}$'"),
            name="ck_provider_operations_fingerprint_format",
        ),
        CheckConstraint(
            (
                "("
                "status = 'pending' "
                "AND started_at IS NULL "
                "AND completed_at IS NULL"
                ") "
                "OR "
                "("
                "status IN ('in_progress', 'failed_retryable') "
                "AND started_at IS NOT NULL "
                "AND completed_at IS NULL"
                ") "
                "OR "
                "("
                "status IN ('succeeded', 'failed_terminal') "
                "AND started_at IS NOT NULL "
                "AND completed_at IS NOT NULL"
                ")"
            ),
            name="ck_provider_operations_status_timestamps",
        ),
        CheckConstraint(
            (
                "("
                "status IN ('failed_retryable', 'failed_terminal') "
                "AND failure_code IS NOT NULL"
                ") "
                "OR "
                "("
                "status NOT IN "
                "('failed_retryable', 'failed_terminal') "
                "AND failure_code IS NULL "
                "AND failure_message IS NULL"
                ")"
            ),
            name="ck_provider_operations_failure_consistency",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_provider_operations_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    billing_customer_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "billing_customers.id",
            name=("fk_provider_operations_billing_customer_id_billing_customers"),
        ),
        nullable=True,
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "subscriptions.id",
            name=("fk_provider_operations_subscription_id_subscriptions"),
        ),
        nullable=True,
    )
    provider: Mapped[BillingProvider] = mapped_column(
        billing_provider_enum,
        nullable=False,
    )
    operation_type: Mapped[ProviderOperationType] = mapped_column(
        provider_operation_type_enum,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    request_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    status: Mapped[ProviderOperationStatus] = mapped_column(
        provider_operation_status_enum,
        nullable=False,
        default=ProviderOperationStatus.PENDING,
        server_default=ProviderOperationStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    provider_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    result_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    failure_message: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    tenant: Mapped[Tenant] = relationship()
    billing_customer: Mapped[BillingCustomer | None] = relationship()
    subscription: Mapped[Subscription | None] = relationship()


class BillingWebhookEvent(Base):
    """Verified provider event persisted before business processing."""

    __tablename__ = "billing_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_billing_webhook_events_provider_event",
        ),
        CheckConstraint(
            ("provider_state_version IS NULL OR provider_state_version >= 0"),
            name="ck_billing_webhook_events_version_nonnegative",
        ),
        CheckConstraint(
            (
                "("
                "status = 'received' "
                "AND processed_at IS NULL "
                "AND failure_code IS NULL "
                "AND failure_message IS NULL"
                ") "
                "OR "
                "("
                "status IN ('processed', 'ignored') "
                "AND processed_at IS NOT NULL "
                "AND failure_code IS NULL "
                "AND failure_message IS NULL"
                ") "
                "OR "
                "("
                "status = 'failed_retryable' "
                "AND processed_at IS NULL "
                "AND failure_code IS NOT NULL"
                ") "
                "OR "
                "("
                "status = 'failed_terminal' "
                "AND processed_at IS NOT NULL "
                "AND failure_code IS NOT NULL"
                ")"
            ),
            name="ck_billing_webhook_events_status_consistency",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    provider: Mapped[BillingProvider] = mapped_column(
        billing_provider_enum,
        nullable=False,
    )
    provider_event_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_state_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    status: Mapped[WebhookEventStatus] = mapped_column(
        webhook_event_status_enum,
        nullable=False,
        default=WebhookEventStatus.RECEIVED,
        server_default=WebhookEventStatus.RECEIVED.value,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    failure_message: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
