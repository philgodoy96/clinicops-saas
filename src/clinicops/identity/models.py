from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM as PostgreSQLEnum
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clinicops.db.base import Base


class UserStatus(StrEnum):
    """Lifecycle status of a global platform user."""

    ACTIVE = "active"
    DISABLED = "disabled"


def _enum_values(enum_class: type[Enum]) -> list[str]:
    """Return persisted string values for a Python enum."""

    return [str(member.value) for member in enum_class]


user_status_enum = PostgreSQLEnum(
    UserStatus,
    name="user_status",
    values_callable=_enum_values,
)


class User(Base):
    """Global platform identity independent from tenant membership."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )
    status: Mapped[UserStatus] = mapped_column(
        user_status_enum,
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
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
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    password_credential: Mapped[PasswordCredential | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
    )


class PasswordCredential(Base):
    """Password authentication material owned by one global user."""

    __tablename__ = "password_credentials"

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_password_credentials_user_id_users",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
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

    user: Mapped[User] = relationship(
        back_populates="password_credential",
    )
