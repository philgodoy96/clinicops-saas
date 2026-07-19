"""add authentication sessions

Revision ID: 0005_add_authentication_sessions
Revises: 0004_add_invitations
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_add_authentication_sessions"
down_revision: str | None = "0004_add_invitations"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

auth_session_status = postgresql.ENUM(
    "active",
    "revoked",
    "compromised",
    name="auth_session_status",
    create_type=False,
)
refresh_token_status = postgresql.ENUM(
    "active",
    "consumed",
    "revoked",
    name="refresh_token_status",
    create_type=False,
)


def upgrade() -> None:
    """Create authentication session and refresh token persistence."""

    bind = op.get_bind()
    auth_session_status.create(bind, checkfirst=True)
    refresh_token_status.create(bind, checkfirst=True)

    op.create_table(
        "auth_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            auth_session_status,
            server_default=sa.text("'active'::auth_session_status"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_rotated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "compromised_at",
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
            "expires_at > created_at",
            name="ck_auth_sessions_expires_after_created_at",
        ),
        sa.CheckConstraint(
            (
                "(status = 'active' "
                "AND revoked_at IS NULL "
                "AND compromised_at IS NULL) "
                "OR "
                "(status = 'revoked' "
                "AND revoked_at IS NOT NULL "
                "AND compromised_at IS NULL) "
                "OR "
                "(status = 'compromised' "
                "AND compromised_at IS NOT NULL "
                "AND revoked_at IS NULL)"
            ),
            name="ck_auth_sessions_status_consistency",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_auth_sessions_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_sessions_user_id_status",
        "auth_sessions",
        ["user_id", "status"],
        unique=False,
    )

    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "token_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "status",
            refresh_token_status,
            server_default=sa.text("'active'::refresh_token_status"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "replaced_by_token_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_refresh_tokens_expires_after_created_at",
        ),
        sa.CheckConstraint(
            (
                "(status = 'active' "
                "AND consumed_at IS NULL "
                "AND revoked_at IS NULL "
                "AND replaced_by_token_id IS NULL) "
                "OR "
                "(status = 'consumed' "
                "AND consumed_at IS NOT NULL "
                "AND revoked_at IS NULL "
                "AND replaced_by_token_id IS NOT NULL) "
                "OR "
                "(status = 'revoked' "
                "AND revoked_at IS NOT NULL "
                "AND consumed_at IS NULL "
                "AND replaced_by_token_id IS NULL)"
            ),
            name="ck_refresh_tokens_status_consistency",
        ),
        sa.CheckConstraint(
            ("replaced_by_token_id IS NULL OR replaced_by_token_id <> id"),
            name="ck_refresh_tokens_not_self_replaced",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name="fk_refresh_tokens_session_id_auth_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_token_id"],
            ["refresh_tokens.id"],
            name=("fk_refresh_tokens_replaced_by_token_id_refresh_tokens"),
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_digest",
            name="uq_refresh_tokens_token_digest",
        ),
    )
    op.create_index(
        "ix_refresh_tokens_session_id_status",
        "refresh_tokens",
        ["session_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_refresh_tokens_one_active_per_session",
        "refresh_tokens",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    """Remove authentication session and refresh token persistence."""

    op.drop_index(
        "uq_refresh_tokens_one_active_per_session",
        table_name="refresh_tokens",
    )
    op.drop_index(
        "ix_refresh_tokens_session_id_status",
        table_name="refresh_tokens",
    )
    op.drop_table("refresh_tokens")

    op.drop_index(
        "ix_auth_sessions_user_id_status",
        table_name="auth_sessions",
    )
    op.drop_table("auth_sessions")

    bind = op.get_bind()
    refresh_token_status.drop(bind, checkfirst=True)
    auth_session_status.drop(bind, checkfirst=True)
