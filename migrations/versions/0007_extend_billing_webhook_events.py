"""Extend billing webhook events for authenticated ingestion.

Revision ID: 0007_extend_billing_webhooks
Revises: 0006_add_billing_persistence
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_extend_billing_webhooks"
down_revision: str | None = "0006_add_billing_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_PAYLOAD_HASH = "0" * 64


def upgrade() -> None:
    context = op.get_context()

    with context.autocommit_block():
        op.execute(
            "ALTER TYPE webhook_event_status "
            "ADD VALUE IF NOT EXISTS 'processing' "
            "BEFORE 'processed'"
        )

    op.add_column(
        "billing_webhook_events",
        sa.Column(
            "provider_subscription_id",
            sa.String(length=255),
            server_default=sa.text("'legacy_unknown'"),
            nullable=False,
        ),
    )
    op.add_column(
        "billing_webhook_events",
        sa.Column(
            "payload_sha256",
            sa.String(length=64),
            server_default=sa.text(f"'{_LEGACY_PAYLOAD_HASH}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "billing_webhook_events",
        sa.Column(
            "signature_timestamp",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "billing_webhook_events",
        sa.Column(
            "processing_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )

    op.execute(
        """
        UPDATE billing_webhook_events
        SET provider_subscription_id = COALESCE(
            payload -> 'data' ->> 'provider_subscription_id',
            payload ->> 'provider_subscription_id',
            'legacy_unknown'
        )
        """
    )
    op.execute(
        """
        UPDATE billing_webhook_events
        SET provider_created_at = created_at
        WHERE provider_created_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE billing_webhook_events
        SET provider_state_version = 1
        WHERE provider_state_version IS NULL
        """
    )

    op.alter_column(
        "billing_webhook_events",
        "provider_created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "billing_webhook_events",
        "provider_state_version",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "billing_webhook_events",
        "provider_subscription_id",
        existing_type=sa.String(length=255),
        server_default=None,
    )
    op.alter_column(
        "billing_webhook_events",
        "payload_sha256",
        existing_type=sa.String(length=64),
        server_default=None,
    )
    op.alter_column(
        "billing_webhook_events",
        "signature_timestamp",
        existing_type=sa.BigInteger(),
        server_default=None,
    )

    op.drop_constraint(
        "ck_billing_webhook_events_version_nonnegative",
        "billing_webhook_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_webhook_events_status_consistency",
        "billing_webhook_events",
        type_="check",
    )

    op.create_check_constraint(
        "ck_billing_webhook_events_subscription_id_nonempty",
        "billing_webhook_events",
        "provider_subscription_id <> ''",
    )
    op.create_check_constraint(
        "ck_billing_webhook_events_version_positive",
        "billing_webhook_events",
        "provider_state_version > 0",
    )
    op.create_check_constraint(
        "ck_billing_webhook_events_signature_timestamp_positive",
        "billing_webhook_events",
        "signature_timestamp > 0",
    )
    op.create_check_constraint(
        "ck_billing_webhook_events_attempt_count_nonnegative",
        "billing_webhook_events",
        "processing_attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_billing_webhook_events_payload_sha256_format",
        "billing_webhook_events",
        "payload_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_billing_webhook_events_payload_object",
        "billing_webhook_events",
        "jsonb_typeof(payload) = 'object'",
    )
    op.create_check_constraint(
        "ck_billing_webhook_events_status_consistency",
        "billing_webhook_events",
        (
            "(status = 'received' "
            "AND processing_attempt_count = 0 "
            "AND processed_at IS NULL "
            "AND failure_code IS NULL "
            "AND failure_message IS NULL) "
            "OR "
            "(status = 'processing' "
            "AND processing_attempt_count > 0 "
            "AND processed_at IS NULL "
            "AND failure_code IS NULL "
            "AND failure_message IS NULL) "
            "OR "
            "(status IN ('processed', 'ignored') "
            "AND processing_attempt_count > 0 "
            "AND processed_at IS NOT NULL "
            "AND failure_code IS NULL "
            "AND failure_message IS NULL) "
            "OR "
            "(status = 'failed_retryable' "
            "AND processing_attempt_count > 0 "
            "AND processed_at IS NULL "
            "AND failure_code IS NOT NULL) "
            "OR "
            "(status = 'failed_terminal' "
            "AND processing_attempt_count > 0 "
            "AND processed_at IS NOT NULL "
            "AND failure_code IS NOT NULL)"
        ),
    )

    op.create_index(
        "ix_billing_webhook_events_status_created_at",
        "billing_webhook_events",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_billing_webhook_events_subscription_version",
        "billing_webhook_events",
        [
            "provider",
            "provider_subscription_id",
            "provider_state_version",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE billing_webhook_events
        SET status = 'received',
            processing_attempt_count = 0,
            processed_at = NULL,
            failure_code = NULL,
            failure_message = NULL
        WHERE status = 'processing'
        """
    )

    op.drop_index(
        "ix_billing_webhook_events_subscription_version",
        table_name="billing_webhook_events",
    )
    op.drop_index(
        "ix_billing_webhook_events_status_created_at",
        table_name="billing_webhook_events",
    )

    op.drop_constraint(
        "ck_billing_webhook_events_status_consistency",
        "billing_webhook_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_webhook_events_payload_object",
        "billing_webhook_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_webhook_events_payload_sha256_format",
        "billing_webhook_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_webhook_events_attempt_count_nonnegative",
        "billing_webhook_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_webhook_events_signature_timestamp_positive",
        "billing_webhook_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_webhook_events_version_positive",
        "billing_webhook_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_billing_webhook_events_subscription_id_nonempty",
        "billing_webhook_events",
        type_="check",
    )

    op.create_check_constraint(
        "ck_billing_webhook_events_version_nonnegative",
        "billing_webhook_events",
        ("provider_state_version IS NULL OR provider_state_version >= 0"),
    )

    op.alter_column(
        "billing_webhook_events",
        "provider_created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        "billing_webhook_events",
        "provider_state_version",
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.drop_column(
        "billing_webhook_events",
        "processing_attempt_count",
    )
    op.drop_column(
        "billing_webhook_events",
        "signature_timestamp",
    )
    op.drop_column(
        "billing_webhook_events",
        "payload_sha256",
    )
    op.drop_column(
        "billing_webhook_events",
        "provider_subscription_id",
    )

    bind = op.get_bind()
    old_status_enum = postgresql.ENUM(
        "received",
        "processed",
        "ignored",
        "failed_retryable",
        "failed_terminal",
        name="webhook_event_status_legacy",
    )
    old_status_enum.create(bind, checkfirst=False)

    op.execute("ALTER TABLE billing_webhook_events ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE billing_webhook_events "
        "ALTER COLUMN status TYPE webhook_event_status_legacy "
        "USING status::text::webhook_event_status_legacy"
    )
    op.execute("DROP TYPE webhook_event_status")
    op.execute("ALTER TYPE webhook_event_status_legacy RENAME TO webhook_event_status")
    op.execute(
        "ALTER TABLE billing_webhook_events "
        "ALTER COLUMN status SET DEFAULT "
        "'received'::webhook_event_status"
    )

    op.create_check_constraint(
        "ck_billing_webhook_events_status_consistency",
        "billing_webhook_events",
        (
            "(status = 'received' "
            "AND processed_at IS NULL "
            "AND failure_code IS NULL "
            "AND failure_message IS NULL) "
            "OR "
            "(status IN ('processed', 'ignored') "
            "AND processed_at IS NOT NULL "
            "AND failure_code IS NULL "
            "AND failure_message IS NULL) "
            "OR "
            "(status = 'failed_retryable' "
            "AND processed_at IS NULL "
            "AND failure_code IS NOT NULL) "
            "OR "
            "(status = 'failed_terminal' "
            "AND processed_at IS NOT NULL "
            "AND failure_code IS NOT NULL)"
        ),
    )
