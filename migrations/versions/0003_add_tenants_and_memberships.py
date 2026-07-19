"""add tenants and memberships

Revision ID: 0003_add_tenancy
Revises: 0002_add_global_identity
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_add_tenancy"
down_revision: str | None = "0002_add_global_identity"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

tenant_status = postgresql.ENUM(
    "active",
    "disabled",
    name="tenant_status",
    create_type=False,
)
membership_status = postgresql.ENUM(
    "active",
    "disabled",
    name="membership_status",
    create_type=False,
)
tenant_role = postgresql.ENUM(
    "owner",
    "admin",
    "staff",
    name="tenant_role",
    create_type=False,
)


def upgrade() -> None:
    """Create tenant and membership persistence."""

    bind = op.get_bind()
    tenant_status.create(bind, checkfirst=True)
    membership_status.create(bind, checkfirst=True)
    tenant_role.create(bind, checkfirst=True)

    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "status",
            tenant_status,
            server_default=sa.text("'active'::tenant_status"),
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
        sa.Column(
            "disabled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "memberships",
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
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "role",
            tenant_role,
            nullable=False,
        ),
        sa.Column(
            "status",
            membership_status,
            server_default=sa.text("'active'::membership_status"),
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
        sa.Column(
            "disabled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_memberships_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_memberships_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "tenant_id",
            name="uq_memberships_user_id_tenant_id",
        ),
    )

    op.create_index(
        "ix_memberships_tenant_id",
        "memberships",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_memberships_one_active_owner_per_tenant",
        "memberships",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner' AND status = 'active'"),
    )


def downgrade() -> None:
    """Remove tenant and membership persistence."""

    op.drop_index(
        "uq_memberships_one_active_owner_per_tenant",
        table_name="memberships",
    )
    op.drop_index(
        "ix_memberships_tenant_id",
        table_name="memberships",
    )
    op.drop_table("memberships")
    op.drop_table("tenants")

    bind = op.get_bind()
    tenant_role.drop(bind, checkfirst=True)
    membership_status.drop(bind, checkfirst=True)
    tenant_status.drop(bind, checkfirst=True)
