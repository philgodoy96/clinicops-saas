"""add invitations

Revision ID: 0004_add_invitations
Revises: 0003_add_tenancy
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_add_invitations"
down_revision: str | None = "0003_add_tenancy"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

invitation_status = postgresql.ENUM(
    "pending",
    "accepted",
    "revoked",
    "expired",
    name="invitation_status",
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
    """Create tenant invitation persistence."""

    bind = op.get_bind()
    invitation_status.create(bind, checkfirst=True)

    op.create_table(
        "invitations",
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
            "invited_email",
            sa.String(length=320),
            nullable=False,
        ),
        sa.Column(
            "role",
            tenant_role,
            nullable=False,
        ),
        sa.Column(
            "status",
            invitation_status,
            server_default=sa.text("'pending'::invitation_status"),
            nullable=False,
        ),
        sa.Column(
            "token_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "created_by_membership_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "accepted_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
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
            "role <> 'owner'",
            name="ck_invitations_role_not_owner",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_invitations_expires_after_created_at",
        ),
        sa.CheckConstraint(
            (
                "(status = 'accepted' "
                "AND accepted_by_user_id IS NOT NULL "
                "AND accepted_at IS NOT NULL "
                "AND revoked_at IS NULL) "
                "OR "
                "(status <> 'accepted' "
                "AND accepted_by_user_id IS NULL "
                "AND accepted_at IS NULL)"
            ),
            name="ck_invitations_accepted_state",
        ),
        sa.CheckConstraint(
            (
                "(status = 'revoked' "
                "AND revoked_at IS NOT NULL "
                "AND accepted_by_user_id IS NULL "
                "AND accepted_at IS NULL) "
                "OR "
                "(status <> 'revoked' "
                "AND revoked_at IS NULL)"
            ),
            name="ck_invitations_revoked_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_invitations_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_membership_id"],
            ["memberships.id"],
            name=("fk_invitations_created_by_membership_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name="fk_invitations_accepted_by_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_digest",
            name="uq_invitations_token_digest",
        ),
    )

    op.create_index(
        "uq_invitations_one_pending_per_tenant_email",
        "invitations",
        ["tenant_id", "invited_email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_invitations_tenant_id_status",
        "invitations",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_invitations_invited_email",
        "invitations",
        ["invited_email"],
        unique=False,
    )


def downgrade() -> None:
    """Remove tenant invitation persistence."""

    op.drop_index(
        "ix_invitations_invited_email",
        table_name="invitations",
    )
    op.drop_index(
        "ix_invitations_tenant_id_status",
        table_name="invitations",
    )
    op.drop_index(
        "uq_invitations_one_pending_per_tenant_email",
        table_name="invitations",
    )
    op.drop_table("invitations")

    bind = op.get_bind()
    invitation_status.drop(bind, checkfirst=True)
