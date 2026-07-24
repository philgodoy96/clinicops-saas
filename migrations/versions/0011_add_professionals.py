"""Add tenant-scoped professionals persistence.

Revision ID: 0011_add_professionals
Revises: 0010_add_patients
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_add_professionals"
down_revision: str | Sequence[str] | None = "0010_add_patients"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

professional_status_enum = postgresql.ENUM(
    "active",
    "archived",
    name="professional_status",
    create_type=False,
)


def upgrade() -> None:
    """Create the Professionals Domain persistence structures."""

    bind = op.get_bind()
    professional_status_enum.create(bind, checkfirst=False)

    op.create_unique_constraint(
        "uq_memberships_tenant_id_id",
        "memberships",
        ["tenant_id", "id"],
    )

    op.create_table(
        "professionals",
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
            "membership_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "full_name",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "specialty",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "registration_number",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "registration_region",
            sa.String(length=50),
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
            professional_status_enum,
            server_default=sa.text("'active'::professional_status"),
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
            name="ck_professionals_full_name_not_blank",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_professionals_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_professionals_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_professionals_tenant_membership_memberships",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_professionals",
        ),
    )

    op.create_index(
        "ix_professionals_tenant_timeline",
        "professionals",
        [
            "tenant_id",
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )
    op.create_index(
        "ix_professionals_tenant_status_timeline",
        "professionals",
        [
            "tenant_id",
            "status",
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )
    op.create_index(
        "uq_professionals_tenant_external_reference",
        "professionals",
        ["tenant_id", "external_reference"],
        unique=True,
        postgresql_where=sa.text("external_reference IS NOT NULL"),
    )
    op.create_index(
        "uq_professionals_membership_id",
        "professionals",
        ["membership_id"],
        unique=True,
        postgresql_where=sa.text("membership_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove the Professionals Domain persistence structures."""

    op.drop_index(
        "uq_professionals_membership_id",
        table_name="professionals",
        postgresql_where=sa.text("membership_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_professionals_tenant_external_reference",
        table_name="professionals",
        postgresql_where=sa.text("external_reference IS NOT NULL"),
    )
    op.drop_index(
        "ix_professionals_tenant_status_timeline",
        table_name="professionals",
    )
    op.drop_index(
        "ix_professionals_tenant_timeline",
        table_name="professionals",
    )
    op.drop_table("professionals")

    op.drop_constraint(
        "uq_memberships_tenant_id_id",
        "memberships",
        type_="unique",
    )

    bind = op.get_bind()
    professional_status_enum.drop(bind, checkfirst=False)
