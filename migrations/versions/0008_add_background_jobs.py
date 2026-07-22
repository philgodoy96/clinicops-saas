"""Add durable background jobs.

Revision ID: 0008_add_background_jobs
Revises: 0007_extend_billing_webhooks
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_add_background_jobs"
down_revision: str | None = "0007_extend_billing_webhooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


background_job_status_enum = postgresql.ENUM(
    "queued",
    "processing",
    "retry_scheduled",
    "succeeded",
    "dead_lettered",
    name="background_job_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    background_job_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "background_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "job_type",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "payload_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            background_job_status_enum,
            server_default=sa.text("'queued'::background_job_status"),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "processing_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "worker_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "claim_token",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_error_code",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "last_error_message",
            sa.String(length=2000),
            nullable=True,
        ),
        sa.Column(
            "last_failed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "dead_lettered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "correlation_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "origin_request_id",
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
        sa.CheckConstraint(
            "payload_version >= 1",
            name="ck_background_jobs_payload_version_positive",
        ),
        sa.CheckConstraint(
            "processing_attempt_count >= 0",
            name=("ck_background_jobs_processing_attempt_count_non_negative"),
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_background_jobs_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "processing_attempt_count <= max_attempts",
            name="ck_background_jobs_attempt_count_within_limit",
        ),
        sa.CheckConstraint(
            "btrim(job_type) <> ''",
            name="ck_background_jobs_job_type_not_empty",
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR btrim(idempotency_key) <> ''",
            name="ck_background_jobs_idempotency_key_not_empty",
        ),
        sa.CheckConstraint(
            "worker_id IS NULL OR btrim(worker_id) <> ''",
            name="ck_background_jobs_worker_id_not_empty",
        ),
        sa.CheckConstraint(
            "btrim(correlation_id) <> ''",
            name="ck_background_jobs_correlation_id_not_empty",
        ),
        sa.CheckConstraint(
            """
            (
                status = 'processing'
                AND worker_id IS NOT NULL
                AND claim_token IS NOT NULL
                AND claimed_at IS NOT NULL
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at > claimed_at
                AND completed_at IS NULL
                AND dead_lettered_at IS NULL
            )
            OR
            (
                status IN ('queued', 'retry_scheduled')
                AND worker_id IS NULL
                AND claim_token IS NULL
                AND claimed_at IS NULL
                AND lease_expires_at IS NULL
                AND completed_at IS NULL
                AND dead_lettered_at IS NULL
            )
            OR
            (
                status = 'succeeded'
                AND worker_id IS NULL
                AND claim_token IS NULL
                AND claimed_at IS NULL
                AND lease_expires_at IS NULL
                AND completed_at IS NOT NULL
                AND dead_lettered_at IS NULL
            )
            OR
            (
                status = 'dead_lettered'
                AND worker_id IS NULL
                AND claim_token IS NULL
                AND claimed_at IS NULL
                AND lease_expires_at IS NULL
                AND completed_at IS NULL
                AND dead_lettered_at IS NOT NULL
            )
            """,
            name="ck_background_jobs_lifecycle_consistency",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_background_jobs",
        ),
    )

    op.create_index(
        "uq_background_jobs_idempotency_key",
        "background_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_index(
        "ix_background_jobs_available",
        "background_jobs",
        [
            sa.text("priority DESC"),
            "available_at",
            "created_at",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'retry_scheduled')"),
    )

    op.create_index(
        "ix_background_jobs_stale_processing",
        "background_jobs",
        [
            "lease_expires_at",
            "created_at",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text("status = 'processing'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_background_jobs_stale_processing",
        table_name="background_jobs",
    )
    op.drop_index(
        "ix_background_jobs_available",
        table_name="background_jobs",
    )
    op.drop_index(
        "uq_background_jobs_idempotency_key",
        table_name="background_jobs",
    )

    op.drop_table("background_jobs")

    bind = op.get_bind()
    background_job_status_enum.drop(bind, checkfirst=True)
