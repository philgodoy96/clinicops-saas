from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PostgreSQLEnum
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clinicops.db.base import Base
from clinicops.patients.enums import PatientStatus
from clinicops.tenancy.models import Tenant


def _enum_values(enum_class: type[Enum]) -> list[str]:
    """Return persisted string values for a Python enum."""

    return [str(member.value) for member in enum_class]


patient_status_enum = PostgreSQLEnum(
    PatientStatus,
    name="patient_status",
    values_callable=_enum_values,
)


class Patient(Base):
    """Tenant-owned operational patient record."""

    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint(
            "btrim(full_name) <> ''",
            name="ck_patients_full_name_not_blank",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_patients_version_positive",
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
            name="fk_patients_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    date_of_birth: Mapped[date | None] = mapped_column(
        Date,
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
    status: Mapped[PatientStatus] = mapped_column(
        patient_status_enum,
        nullable=False,
        default=PatientStatus.ACTIVE,
        server_default=PatientStatus.ACTIVE.value,
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

    tenant: Mapped[Tenant] = relationship()


Index(
    "ix_patients_tenant_timeline",
    Patient.tenant_id,
    Patient.created_at.desc(),
    Patient.id.desc(),
)

Index(
    "ix_patients_tenant_status_timeline",
    Patient.tenant_id,
    Patient.status,
    Patient.created_at.desc(),
    Patient.id.desc(),
)

Index(
    "uq_patients_tenant_external_reference",
    Patient.tenant_id,
    Patient.external_reference,
    unique=True,
    postgresql_where=(Patient.external_reference.is_not(None)),
)


__all__ = ["Patient", "patient_status_enum"]
