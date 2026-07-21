"""Add billing persistence.

Revision ID: 0006_add_billing_persistence
Revises: 0005_add_authentication_sessions
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_add_billing_persistence"
down_revision: str | None = "0005_add_authentication_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


billing_plan_enum = postgresql.ENUM(
    "starter",
    "professional",
    name="billing_plan",
    create_type=False,
)

billing_interval_enum = postgresql.ENUM(
    "monthly",
    "yearly",
    name="billing_interval",
    create_type=False,
)

subscription_status_enum = postgresql.ENUM(
    "pending",
    "active",
    "past_due",
    "canceled",
    name="subscription_status",
    create_type=False,
)

billing_provider_enum = postgresql.ENUM(
    "fake",
    name="billing_provider",
    create_type=False,
)

provider_operation_type_enum = postgresql.ENUM(
    "create_customer",
    "create_subscription",
    "change_plan",
    "cancel_subscription",
    name="provider_operation_type",
    create_type=False,
)

provider_operation_status_enum = postgresql.ENUM(
    "pending",
    "in_progress",
    "succeeded",
    "failed_retryable",
    "failed_terminal",
    name="provider_operation_status",
    create_type=False,
)

webhook_event_status_enum = postgresql.ENUM(
    "received",
    "processed",
    "ignored",
    "failed_retryable",
    "failed_terminal",
    name="webhook_event_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    billing_plan_enum.create(bind, checkfirst=True)
    billing_interval_enum.create(bind, checkfirst=True)
    subscription_status_enum.create(bind, checkfirst=True)
    billing_provider_enum.create(bind, checkfirst=True)
    provider_operation_type_enum.create(bind, checkfirst=True)
    provider_operation_status_enum.create(bind, checkfirst=True)
    webhook_event_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "billing_customers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "provider",
            billing_provider_enum,
            nullable=False,
        ),
        sa.Column(
            "provider_customer_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_billing_customers_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_billing_customers",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            name="uq_billing_customers_tenant_id_provider",
        ),
    )
    op.create_index(
        "uq_billing_customers_provider_customer_id",
        "billing_customers",
        ["provider", "provider_customer_id"],
        unique=True,
        postgresql_where=sa.text("provider_customer_id IS NOT NULL"),
    )

    op.create_table(
        "subscriptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "billing_customer_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "provider",
            billing_provider_enum,
            nullable=False,
        ),
        sa.Column(
            "provider_subscription_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "price_code",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "plan",
            billing_plan_enum,
            nullable=False,
        ),
        sa.Column(
            "billing_interval",
            billing_interval_enum,
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
        ),
        sa.Column(
            "unit_amount",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "pending_price_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "status",
            subscription_status_enum,
            server_default=sa.text("'pending'::subscription_status"),
            nullable=False,
        ),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "cancellation_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "current_period_start",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "current_period_end",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "provider_state_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "last_provider_event_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "canceled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "unit_amount > 0",
            name="ck_subscriptions_unit_amount_positive",
        ),
        sa.CheckConstraint(
            "provider_state_version >= 0",
            name=("ck_subscriptions_provider_state_version_nonnegative"),
        ),
        sa.CheckConstraint(
            ("pending_price_code IS NULL OR pending_price_code <> price_code"),
            name="ck_subscriptions_pending_price_differs",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            (
                "status NOT IN ('active', 'past_due') "
                "OR "
                "(current_period_start IS NOT NULL "
                "AND current_period_end IS NOT NULL)"
            ),
            name="ck_subscriptions_active_period",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["billing_customer_id"],
            ["billing_customers.id"],
            name=("fk_subscriptions_billing_customer_id_billing_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_subscriptions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_subscriptions",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            name="uq_subscriptions_tenant_id",
        ),
    )
    op.create_index(
        "ix_subscriptions_billing_customer_id",
        "subscriptions",
        ["billing_customer_id"],
        unique=False,
    )
    op.create_index(
        "uq_subscriptions_provider_subscription_id",
        "subscriptions",
        ["provider", "provider_subscription_id"],
        unique=True,
        postgresql_where=sa.text("provider_subscription_id IS NOT NULL"),
    )

    op.create_table(
        "provider_operations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "billing_customer_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "provider",
            billing_provider_enum,
            nullable=False,
        ),
        sa.Column(
            "operation_type",
            provider_operation_type_enum,
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "request_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            provider_operation_status_enum,
            server_default=sa.text("'pending'::provider_operation_status"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "provider_reference",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "result_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "failure_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "failure_message",
            sa.String(length=512),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=("ck_provider_operations_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            ("request_fingerprint ~ '^[0-9a-f]{64}$'"),
            name="ck_provider_operations_fingerprint_format",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["billing_customer_id"],
            ["billing_customers.id"],
            name=("fk_provider_operations_billing_customer_id_billing_customers"),
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name=("fk_provider_operations_subscription_id_subscriptions"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_provider_operations_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_provider_operations",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "operation_type",
            "idempotency_key",
            name="uq_provider_operations_tenant_op_idempotency",
        ),
    )
    op.create_index(
        "ix_provider_operations_provider_reference",
        "provider_operations",
        ["provider_reference"],
        unique=False,
        postgresql_where=sa.text("provider_reference IS NOT NULL"),
    )
    op.create_index(
        "ix_provider_operations_tenant_id_status",
        "provider_operations",
        ["tenant_id", "status"],
        unique=False,
    )

    op.create_table(
        "billing_webhook_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "provider",
            billing_provider_enum,
            nullable=False,
        ),
        sa.Column(
            "provider_event_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "provider_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "provider_state_version",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            webhook_event_status_enum,
            server_default=sa.text("'received'::webhook_event_status"),
            nullable=False,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "failure_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "failure_message",
            sa.String(length=512),
            nullable=True,
        ),
        sa.Column(
            "correlation_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            ("provider_state_version IS NULL OR provider_state_version >= 0"),
            name="ck_billing_webhook_events_version_nonnegative",
        ),
        sa.CheckConstraint(
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
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_billing_webhook_events",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_billing_webhook_events_provider_event",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("billing_webhook_events")

    op.drop_index(
        "ix_provider_operations_tenant_id_status",
        table_name="provider_operations",
    )
    op.drop_index(
        "ix_provider_operations_provider_reference",
        table_name="provider_operations",
        postgresql_where=sa.text("provider_reference IS NOT NULL"),
    )
    op.drop_table("provider_operations")

    op.drop_index(
        "uq_subscriptions_provider_subscription_id",
        table_name="subscriptions",
        postgresql_where=sa.text("provider_subscription_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_subscriptions_billing_customer_id",
        table_name="subscriptions",
    )
    op.drop_table("subscriptions")

    op.drop_index(
        "uq_billing_customers_provider_customer_id",
        table_name="billing_customers",
        postgresql_where=sa.text("provider_customer_id IS NOT NULL"),
    )
    op.drop_table("billing_customers")

    webhook_event_status_enum.drop(bind, checkfirst=True)
    provider_operation_status_enum.drop(bind, checkfirst=True)
    provider_operation_type_enum.drop(bind, checkfirst=True)
    billing_provider_enum.drop(bind, checkfirst=True)
    subscription_status_enum.drop(bind, checkfirst=True)
    billing_interval_enum.drop(bind, checkfirst=True)
    billing_plan_enum.drop(bind, checkfirst=True)
