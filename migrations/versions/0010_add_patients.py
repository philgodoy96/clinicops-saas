"""Add tenant-scoped patient persistence.

Revision ID: 0010_add_patients
Revises: 0009_add_audit_log_entries
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_add_patients"
down_revision: str | None = "0009_add_audit_log_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


patient_status_enum = postgresql.ENUM(
    "active",
    "archived",
    name="patient_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    patient_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "patients",
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
            "full_name",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "date_of_birth",
            sa.Date(),
            nullable=True,
        ),
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=True,
        ),
        sa.Column(
            "phone",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "external_reference",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "status",
            patient_status_enum,
            server_default=sa.text("'active'::patient_status"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
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
            "btrim(full_name) <> ''",
            name="ck_patients_full_name_not_blank",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_patients_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_patients_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_patients",
        ),
    )

    op.create_index(
        "ix_patients_tenant_timeline",
        "patients",
        [
            "tenant_id",
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )

    op.create_index(
        "ix_patients_tenant_status_timeline",
        "patients",
        [
            "tenant_id",
            "status",
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )

    op.create_index(
        "uq_patients_tenant_external_reference",
        "patients",
        [
            "tenant_id",
            "external_reference",
        ],
        unique=True,
        postgresql_where=sa.text("external_reference IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_patients_tenant_external_reference",
        table_name="patients",
        postgresql_where=sa.text("external_reference IS NOT NULL"),
    )

    op.drop_index(
        "ix_patients_tenant_status_timeline",
        table_name="patients",
    )

    op.drop_index(
        "ix_patients_tenant_timeline",
        table_name="patients",
    )

    op.drop_table("patients")

    bind = op.get_bind()
    patient_status_enum.drop(bind, checkfirst=True)
