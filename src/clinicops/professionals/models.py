from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PostgreSQLEnum
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from clinicops.db.base import Base
from clinicops.professionals.enums import ProfessionalStatus


def _enum_values(enum_class: type[Enum]) -> list[str]:
    """Return persisted string values for a Python enum."""

    return [str(member.value) for member in enum_class]


professional_status_enum = PostgreSQLEnum(
    ProfessionalStatus,
    name="professional_status",
    values_callable=_enum_values,
)


class Professional(Base):
    """Tenant-owned operational clinical provider profile."""

    __tablename__ = "professionals"
    __table_args__ = (
        CheckConstraint(
            "btrim(full_name) <> ''",
            name="ck_professionals_full_name_not_blank",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_professionals_version_positive",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_professionals_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_professionals_tenant_membership_memberships",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    membership_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    full_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    specialty: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    registration_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    registration_region: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )
    phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    external_reference: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    status: Mapped[ProfessionalStatus] = mapped_column(
        professional_status_enum,
        nullable=False,
        default=ProfessionalStatus.ACTIVE,
        server_default=ProfessionalStatus.ACTIVE.value,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
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


Index(
    "ix_professionals_tenant_timeline",
    Professional.tenant_id,
    Professional.created_at.desc(),
    Professional.id.desc(),
)

Index(
    "ix_professionals_tenant_status_timeline",
    Professional.tenant_id,
    Professional.status,
    Professional.created_at.desc(),
    Professional.id.desc(),
)

Index(
    "uq_professionals_tenant_external_reference",
    Professional.tenant_id,
    Professional.external_reference,
    unique=True,
    postgresql_where=(Professional.external_reference.is_not(None)),
)

Index(
    "uq_professionals_membership_id",
    Professional.membership_id,
    unique=True,
    postgresql_where=(Professional.membership_id.is_not(None)),
)


__all__ = ["Professional", "professional_status_enum"]
