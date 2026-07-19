from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PostgreSQLEnum
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clinicops.db.base import Base
from clinicops.identity.models import User


class AuthSessionStatus(StrEnum):
    """Lifecycle status of a global authentication session."""

    ACTIVE = "active"
    REVOKED = "revoked"
    COMPROMISED = "compromised"


class RefreshTokenStatus(StrEnum):
    """Lifecycle status of a persisted refresh token."""

    ACTIVE = "active"
    CONSUMED = "consumed"
    REVOKED = "revoked"


def _enum_values(enum_class: type[Enum]) -> list[str]:
    """Return persisted string values for a Python enum."""

    return [str(member.value) for member in enum_class]


auth_session_status_enum = PostgreSQLEnum(
    AuthSessionStatus,
    name="auth_session_status",
    values_callable=_enum_values,
)
refresh_token_status_enum = PostgreSQLEnum(
    RefreshTokenStatus,
    name="refresh_token_status",
    values_callable=_enum_values,
)


class AuthSession(Base):
    """Persistent global authentication session for one user."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_sessions_expires_after_created_at",
        ),
        CheckConstraint(
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
        Index(
            "ix_auth_sessions_user_id_status",
            "user_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_auth_sessions_user_id_users",
        ),
        nullable=False,
    )
    status: Mapped[AuthSessionStatus] = mapped_column(
        auth_session_status_enum,
        nullable=False,
        default=AuthSessionStatus.ACTIVE,
        server_default=AuthSessionStatus.ACTIVE.value,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    compromised_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship()
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="auth_session",
        cascade="all, delete-orphan",
    )


class RefreshToken(Base):
    """Persistent digest and lifecycle state for a refresh token."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint(
            "token_digest",
            name="uq_refresh_tokens_token_digest",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_refresh_tokens_expires_after_created_at",
        ),
        CheckConstraint(
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
        CheckConstraint(
            ("replaced_by_token_id IS NULL OR replaced_by_token_id <> id"),
            name="ck_refresh_tokens_not_self_replaced",
        ),
        Index(
            "uq_refresh_tokens_one_active_per_session",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_refresh_tokens_session_id_status",
            "session_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "auth_sessions.id",
            name="fk_refresh_tokens_session_id_auth_sessions",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    token_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[RefreshTokenStatus] = mapped_column(
        refresh_token_status_enum,
        nullable=False,
        default=RefreshTokenStatus.ACTIVE,
        server_default=RefreshTokenStatus.ACTIVE.value,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    replaced_by_token_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "refresh_tokens.id",
            name=("fk_refresh_tokens_replaced_by_token_id_refresh_tokens"),
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    auth_session: Mapped[AuthSession] = relationship(
        back_populates="refresh_tokens",
        foreign_keys=[session_id],
    )
    replacement: Mapped[RefreshToken | None] = relationship(
        foreign_keys=[replaced_by_token_id],
        remote_side=lambda: [RefreshToken.id],
    )
