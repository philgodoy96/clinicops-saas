from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import (
    ENUM as PostgreSQLEnum,
)
from sqlalchemy.dialects.postgresql import (
    JSONB,
)
from sqlalchemy.dialects.postgresql import (
    UUID as PostgreSQLUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from clinicops.db.base import Base
from clinicops.jobs.enums import BackgroundJobStatus


def _enum_values(enum_class: type[Enum]) -> list[str]:
    return [str(member.value) for member in enum_class]


background_job_status_enum = PostgreSQLEnum(
    BackgroundJobStatus,
    name="background_job_status",
    values_callable=_enum_values,
)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    __table_args__ = (
        CheckConstraint(
            "payload_version >= 1",
            name="ck_background_jobs_payload_version_positive",
        ),
        CheckConstraint(
            "processing_attempt_count >= 0",
            name="ck_background_jobs_processing_attempt_count_non_negative",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_background_jobs_max_attempts_positive",
        ),
        CheckConstraint(
            "processing_attempt_count <= max_attempts",
            name="ck_background_jobs_attempt_count_within_limit",
        ),
        CheckConstraint(
            "btrim(job_type) <> ''",
            name="ck_background_jobs_job_type_not_empty",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR btrim(idempotency_key) <> ''",
            name="ck_background_jobs_idempotency_key_not_empty",
        ),
        CheckConstraint(
            "worker_id IS NULL OR btrim(worker_id) <> ''",
            name="ck_background_jobs_worker_id_not_empty",
        ),
        CheckConstraint(
            "btrim(correlation_id) <> ''",
            name="ck_background_jobs_correlation_id_not_empty",
        ),
        CheckConstraint(
            """
            (
                status = 'processing'
                AND worker_id IS NOT NULL
                AND claim_token IS NOT NULL
                AND claimed_at IS NOT NULL
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at > claimed_at
                AND completed_at IS NULL
                AND dead_lettered_at IS NULL
            )
            OR
            (
                status IN ('queued', 'retry_scheduled')
                AND worker_id IS NULL
                AND claim_token IS NULL
                AND claimed_at IS NULL
                AND lease_expires_at IS NULL
                AND completed_at IS NULL
                AND dead_lettered_at IS NULL
            )
            OR
            (
                status = 'succeeded'
                AND worker_id IS NULL
                AND claim_token IS NULL
                AND claimed_at IS NULL
                AND lease_expires_at IS NULL
                AND completed_at IS NOT NULL
                AND dead_lettered_at IS NULL
            )
            OR
            (
                status = 'dead_lettered'
                AND worker_id IS NULL
                AND claim_token IS NULL
                AND claimed_at IS NULL
                AND lease_expires_at IS NULL
                AND completed_at IS NULL
                AND dead_lettered_at IS NOT NULL
            )
            """,
            name="ck_background_jobs_lifecycle_consistency",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    job_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    payload_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )

    status: Mapped[BackgroundJobStatus] = mapped_column(
        background_job_status_enum,
        nullable=False,
        default=BackgroundJobStatus.QUEUED,
        server_default=BackgroundJobStatus.QUEUED.value,
    )

    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    processing_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        server_default="5",
    )

    worker_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    claim_token: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )

    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    last_error_message: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
    )

    last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    correlation_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    origin_request_id: Mapped[str | None] = mapped_column(
        String(255),
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


Index(
    "uq_background_jobs_idempotency_key",
    BackgroundJob.idempotency_key,
    unique=True,
    postgresql_where=BackgroundJob.idempotency_key.is_not(None),
)

Index(
    "ix_background_jobs_available",
    BackgroundJob.priority.desc(),
    BackgroundJob.available_at.asc(),
    BackgroundJob.created_at.asc(),
    BackgroundJob.id.asc(),
    postgresql_where=BackgroundJob.status.in_(
        (
            BackgroundJobStatus.QUEUED,
            BackgroundJobStatus.RETRY_SCHEDULED,
        )
    ),
)

Index(
    "ix_background_jobs_stale_processing",
    BackgroundJob.lease_expires_at.asc(),
    BackgroundJob.created_at.asc(),
    BackgroundJob.id.asc(),
    postgresql_where=(BackgroundJob.status == BackgroundJobStatus.PROCESSING),
)


__all__ = [
    "BackgroundJob",
    "background_job_status_enum",
]
