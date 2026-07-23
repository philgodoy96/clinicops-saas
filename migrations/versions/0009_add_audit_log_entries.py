"""Add durable audit log entries.

Revision ID: 0009_add_audit_log_entries
Revises: 0008_add_background_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_add_audit_log_entries"
down_revision: str | None = "0008_add_background_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log_entries",
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
            "actor_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "actor_role",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "resource_type",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "metadata_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "request_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "correlation_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'system')",
            name="ck_audit_log_entries_actor_type",
        ),
        sa.CheckConstraint(
            "source IN ('http', 'worker', 'cli', 'system')",
            name="ck_audit_log_entries_source",
        ),
        sa.CheckConstraint(
            "metadata_version >= 1",
            name="ck_audit_log_entries_metadata_version",
        ),
        sa.CheckConstraint(
            "btrim(action) <> ''",
            name="ck_audit_log_entries_action_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(resource_type) <> ''",
            name=("ck_audit_log_entries_resource_type_not_blank"),
        ),
        sa.CheckConstraint(
            "btrim(resource_id) <> ''",
            name=("ck_audit_log_entries_resource_id_not_blank"),
        ),
        sa.CheckConstraint(
            "btrim(correlation_id) <> ''",
            name=("ck_audit_log_entries_correlation_id_not_blank"),
        ),
        sa.CheckConstraint(
            ("request_id IS NULL OR btrim(request_id) <> ''"),
            name=("ck_audit_log_entries_request_id_not_blank"),
        ),
        sa.CheckConstraint(
            ("idempotency_key IS NULL OR btrim(idempotency_key) <> ''"),
            name=("ck_audit_log_entries_idempotency_key_not_blank"),
        ),
        sa.CheckConstraint(
            ("actor_role IS NULL OR btrim(actor_role) <> ''"),
            name=("ck_audit_log_entries_actor_role_not_blank"),
        ),
        sa.CheckConstraint(
            (
                "("
                "actor_type = 'user' "
                "AND actor_user_id IS NOT NULL"
                ") OR ("
                "actor_type = 'system' "
                "AND actor_user_id IS NULL "
                "AND actor_role IS NULL"
                ")"
            ),
            name=("ck_audit_log_entries_actor_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=("fk_audit_log_entries_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=("fk_audit_log_entries_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_audit_log_entries",
        ),
    )

    op.create_index(
        "ix_audit_log_entries_tenant_timeline",
        "audit_log_entries",
        [
            "tenant_id",
            sa.text("recorded_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )

    op.create_index(
        "ix_audit_log_entries_tenant_action_timeline",
        "audit_log_entries",
        [
            "tenant_id",
            "action",
            sa.text("recorded_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )

    op.create_index(
        "ix_audit_log_entries_tenant_resource_timeline",
        "audit_log_entries",
        [
            "tenant_id",
            "resource_type",
            "resource_id",
            sa.text("recorded_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )

    op.create_index(
        "uq_audit_log_entries_idempotency_key",
        "audit_log_entries",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_audit_log_entries_idempotency_key",
        table_name="audit_log_entries",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.drop_index(
        "ix_audit_log_entries_tenant_resource_timeline",
        table_name="audit_log_entries",
    )

    op.drop_index(
        "ix_audit_log_entries_tenant_action_timeline",
        table_name="audit_log_entries",
    )

    op.drop_index(
        "ix_audit_log_entries_tenant_timeline",
        table_name="audit_log_entries",
    )

    op.drop_table("audit_log_entries")
