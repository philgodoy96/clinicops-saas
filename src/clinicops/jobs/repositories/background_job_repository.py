from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.models import BackgroundJob


class BackgroundJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(
        self,
        job_id: UUID,
    ) -> BackgroundJob | None:
        return self._session.get(BackgroundJob, job_id)

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> BackgroundJob | None:
        statement = select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key)

        return self._session.execute(statement).scalar_one_or_none()

    def add_and_flush(
        self,
        job: BackgroundJob,
    ) -> BackgroundJob:
        self._session.add(job)
        self._session.flush()
        return job

    def insert_idempotent_or_get_existing(
        self,
        job: BackgroundJob,
    ) -> tuple[BackgroundJob, bool]:
        if job.idempotency_key is None:
            raise ValueError("An idempotency key is required for an idempotent insert.")

        values: dict[str, object] = {
            "id": job.id,
            "job_type": job.job_type,
            "payload_version": job.payload_version,
            "payload": job.payload,
            "status": job.status,
            "idempotency_key": job.idempotency_key,
            "priority": job.priority,
            "processing_attempt_count": (job.processing_attempt_count),
            "max_attempts": job.max_attempts,
            "correlation_id": job.correlation_id,
            "origin_request_id": job.origin_request_id,
        }

        if job.available_at is not None:
            values["available_at"] = job.available_at

        statement = (
            insert(BackgroundJob)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    BackgroundJob.idempotency_key,
                ],
                index_where=(BackgroundJob.idempotency_key.is_not(None)),
            )
            .returning(BackgroundJob.id)
        )

        inserted_id = self._session.execute(statement).scalar_one_or_none()

        if inserted_id is not None:
            inserted_job = self.get_by_id(inserted_id)

            if inserted_job is None:
                raise RuntimeError("The inserted background job could not be reloaded.")

            return inserted_job, True

        existing_job = self.get_by_idempotency_key(job.idempotency_key)

        if existing_job is None:
            raise RuntimeError("The conflicting background job could not be loaded.")

        return existing_job, False

    def get_database_time(self) -> datetime:
        database_time = self._session.execute(select(func.now())).scalar_one_or_none()

        if database_time is None:
            raise RuntimeError("PostgreSQL current time could not be retrieved.")

        return database_time

    def claim_available_for_update_skip_locked(
        self,
        *,
        batch_size: int,
    ) -> list[BackgroundJob]:
        statement = (
            select(BackgroundJob)
            .where(
                BackgroundJob.status.in_(
                    (
                        BackgroundJobStatus.QUEUED,
                        BackgroundJobStatus.RETRY_SCHEDULED,
                    )
                ),
                BackgroundJob.available_at <= func.now(),
                (BackgroundJob.processing_attempt_count < BackgroundJob.max_attempts),
            )
            .order_by(
                BackgroundJob.priority.desc(),
                BackgroundJob.available_at.asc(),
                BackgroundJob.created_at.asc(),
                BackgroundJob.id.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        return list(self._session.scalars(statement).all())

    def find_stale_for_update_skip_locked(
        self,
        *,
        batch_size: int,
        stale_before: datetime,
    ) -> list[BackgroundJob]:
        statement = (
            select(BackgroundJob)
            .where(
                BackgroundJob.status == BackgroundJobStatus.PROCESSING,
                BackgroundJob.lease_expires_at.is_not(None),
                BackgroundJob.lease_expires_at <= stale_before,
            )
            .order_by(
                BackgroundJob.lease_expires_at.asc(),
                BackgroundJob.created_at.asc(),
                BackgroundJob.id.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        return list(self._session.execute(statement).scalars().all())

    def get_by_id_for_update(
        self,
        job_id: UUID,
    ) -> BackgroundJob | None:
        statement = select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()

        return self._session.execute(statement).scalar_one_or_none()

    def flush(self) -> None:
        self._session.flush()


__all__ = ["BackgroundJobRepository"]
