from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import (
    JSONB,
)
from sqlalchemy.dialects.postgresql import (
    UUID as PostgreSQLUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from clinicops.db.base import Base


class AuditLogEntry(Base):
    __tablename__ = "audit_log_entries"

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'system')",
            name="ck_audit_log_entries_actor_type",
        ),
        CheckConstraint(
            "source IN ('http', 'worker', 'cli', 'system')",
            name="ck_audit_log_entries_source",
        ),
        CheckConstraint(
            "metadata_version >= 1",
            name="ck_audit_log_entries_metadata_version",
        ),
        CheckConstraint(
            "btrim(action) <> ''",
            name="ck_audit_log_entries_action_not_blank",
        ),
        CheckConstraint(
            "btrim(resource_type) <> ''",
            name=("ck_audit_log_entries_resource_type_not_blank"),
        ),
        CheckConstraint(
            "btrim(resource_id) <> ''",
            name=("ck_audit_log_entries_resource_id_not_blank"),
        ),
        CheckConstraint(
            "btrim(correlation_id) <> ''",
            name=("ck_audit_log_entries_correlation_id_not_blank"),
        ),
        CheckConstraint(
            ("request_id IS NULL OR btrim(request_id) <> ''"),
            name=("ck_audit_log_entries_request_id_not_blank"),
        ),
        CheckConstraint(
            ("idempotency_key IS NULL OR btrim(idempotency_key) <> ''"),
            name=("ck_audit_log_entries_idempotency_key_not_blank"),
        ),
        CheckConstraint(
            ("actor_role IS NULL OR btrim(actor_role) <> ''"),
            name=("ck_audit_log_entries_actor_role_not_blank"),
        ),
        CheckConstraint(
            (
                "("
                "actor_type = 'user' "
                "AND actor_user_id IS NOT NULL"
                ") OR ("
                "actor_type = 'system' "
                "AND actor_user_id IS NULL "
                "AND actor_role IS NULL"
                ")"
            ),
            name=("ck_audit_log_entries_actor_consistency"),
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
            name=("fk_audit_log_entries_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    actor_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name=("fk_audit_log_entries_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    actor_role: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    resource_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    resource_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    metadata_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    request_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    correlation_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_audit_log_entries_tenant_timeline",
    AuditLogEntry.tenant_id,
    AuditLogEntry.recorded_at.desc(),
    AuditLogEntry.id.desc(),
)

Index(
    "ix_audit_log_entries_tenant_action_timeline",
    AuditLogEntry.tenant_id,
    AuditLogEntry.action,
    AuditLogEntry.recorded_at.desc(),
    AuditLogEntry.id.desc(),
)

Index(
    "ix_audit_log_entries_tenant_resource_timeline",
    AuditLogEntry.tenant_id,
    AuditLogEntry.resource_type,
    AuditLogEntry.resource_id,
    AuditLogEntry.recorded_at.desc(),
    AuditLogEntry.id.desc(),
)

Index(
    "uq_audit_log_entries_idempotency_key",
    AuditLogEntry.idempotency_key,
    unique=True,
    postgresql_where=(AuditLogEntry.idempotency_key.is_not(None)),
)


__all__ = ["AuditLogEntry"]
