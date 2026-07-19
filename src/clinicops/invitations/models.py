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
from clinicops.tenancy.models import (
    Membership,
    Tenant,
    TenantRole,
    tenant_role_enum,
)


class InvitationStatus(StrEnum):
    """Lifecycle status of a tenant invitation."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


def _enum_values(enum_class: type[Enum]) -> list[str]:
    """Return persisted string values for a Python enum."""

    return [str(member.value) for member in enum_class]


invitation_status_enum = PostgreSQLEnum(
    InvitationStatus,
    name="invitation_status",
    values_callable=_enum_values,
)


class Invitation(Base):
    """One-time invitation to create a membership in a tenant."""

    __tablename__ = "invitations"
    __table_args__ = (
        UniqueConstraint(
            "token_digest",
            name="uq_invitations_token_digest",
        ),
        CheckConstraint(
            "role <> 'owner'",
            name="ck_invitations_role_not_owner",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_invitations_expires_after_created_at",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
        Index(
            "uq_invitations_one_pending_per_tenant_email",
            "tenant_id",
            "invited_email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_invitations_tenant_id_status",
            "tenant_id",
            "status",
        ),
        Index(
            "ix_invitations_invited_email",
            "invited_email",
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
            name="fk_invitations_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    invited_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )
    role: Mapped[TenantRole] = mapped_column(
        tenant_role_enum,
        nullable=False,
    )
    status: Mapped[InvitationStatus] = mapped_column(
        invitation_status_enum,
        nullable=False,
        default=InvitationStatus.PENDING,
        server_default=InvitationStatus.PENDING.value,
    )
    token_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    created_by_membership_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "memberships.id",
            name=("fk_invitations_created_by_membership_id_memberships"),
        ),
        nullable=False,
    )
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_invitations_accepted_by_user_id_users",
        ),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
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

    tenant: Mapped[Tenant] = relationship()
    created_by_membership: Mapped[Membership] = relationship(
        foreign_keys=[created_by_membership_id],
    )
    accepted_by_user: Mapped[User | None] = relationship(
        foreign_keys=[accepted_by_user_id],
    )
