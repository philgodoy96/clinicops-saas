from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
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


class TenantStatus(StrEnum):
    """Lifecycle status of a tenant."""

    ACTIVE = "active"
    DISABLED = "disabled"


class MembershipStatus(StrEnum):
    """Lifecycle status of a tenant membership."""

    ACTIVE = "active"
    DISABLED = "disabled"


class TenantRole(StrEnum):
    """Persisted role held by a user within one tenant."""

    OWNER = "owner"
    ADMIN = "admin"
    STAFF = "staff"


def _enum_values(enum_class: type[Enum]) -> list[str]:
    """Return persisted string values for a Python enum."""

    return [str(member.value) for member in enum_class]


tenant_status_enum = PostgreSQLEnum(
    TenantStatus,
    name="tenant_status",
    values_callable=_enum_values,
)
membership_status_enum = PostgreSQLEnum(
    MembershipStatus,
    name="membership_status",
    values_callable=_enum_values,
)
tenant_role_enum = PostgreSQLEnum(
    TenantRole,
    name="tenant_role",
    values_callable=_enum_values,
)


class Tenant(Base):
    """Tenant boundary for clinic data and authorization."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )
    status: Mapped[TenantStatus] = mapped_column(
        tenant_status_enum,
        nullable=False,
        default=TenantStatus.ACTIVE,
        server_default=TenantStatus.ACTIVE.value,
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

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )


class Membership(Base):
    """A global user's role and lifecycle within one tenant."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "tenant_id",
            name="uq_memberships_user_id_tenant_id",
        ),
        Index(
            "ix_memberships_tenant_id",
            "tenant_id",
        ),
        Index(
            "uq_memberships_one_active_owner_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("role = 'owner' AND status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "tenants.id",
            name="fk_memberships_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_memberships_user_id_users",
        ),
        nullable=False,
    )
    role: Mapped[TenantRole] = mapped_column(
        tenant_role_enum,
        nullable=False,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        membership_status_enum,
        nullable=False,
        default=MembershipStatus.ACTIVE,
        server_default=MembershipStatus.ACTIVE.value,
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

    tenant: Mapped[Tenant] = relationship(
        back_populates="memberships",
    )
    user: Mapped[User] = relationship()
