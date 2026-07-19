"""add global identity

Revision ID: 0002_add_global_identity
Revises: 0001_initialize_schema
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_add_global_identity"
down_revision: str | None = "0001_initialize_schema"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

user_status = postgresql.ENUM(
    "active",
    "disabled",
    name="user_status",
    create_type=False,
)


def upgrade() -> None:
    """Create global user and password credential persistence."""

    bind = op.get_bind()
    user_status.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=False,
        ),
        sa.Column(
            "status",
            user_status,
            server_default=sa.text("'active'::user_status"),
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
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "password_credentials",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "password_hash",
            sa.String(length=512),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_password_credentials_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    """Remove global user and password credential persistence."""

    op.drop_table("password_credentials")
    op.drop_table("users")

    bind = op.get_bind()
    user_status.drop(bind, checkfirst=True)
