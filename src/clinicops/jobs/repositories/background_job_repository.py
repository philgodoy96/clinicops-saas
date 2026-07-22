from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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


__all__ = ["BackgroundJobRepository"]
