from collections.abc import Iterable
from typing import cast

from clinicops.jobs.runtime.exceptions import (
    DuplicateJobHandlerError,
    InvalidJobHandlerError,
    UnknownJobTypeError,
)
from clinicops.jobs.runtime.handler import JobHandler

_MAX_JOB_TYPE_LENGTH = 100


class JobHandlerRegistry:
    def __init__(
        self,
        handlers: Iterable[JobHandler] = (),
    ) -> None:
        self._handlers: dict[str, JobHandler] = {}

        for handler in handlers:
            self.register(handler)

    def register(
        self,
        handler: JobHandler,
    ) -> None:
        validated_handler = _validate_handler(handler)
        job_type = _normalize_job_type(validated_handler.job_type)

        if job_type in self._handlers:
            raise DuplicateJobHandlerError(job_type)

        self._handlers[job_type] = validated_handler

    def resolve(
        self,
        job_type: str,
    ) -> JobHandler:
        normalized_job_type = _normalize_job_type(job_type)

        handler = self._handlers.get(normalized_job_type)

        if handler is None:
            raise UnknownJobTypeError(normalized_job_type)

        return handler

    @property
    def registered_job_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


def _validate_handler(
    handler: object,
) -> JobHandler:
    job_type = getattr(handler, "job_type", None)
    supported_payload_version = getattr(
        handler,
        "supported_payload_version",
        None,
    )
    execute = getattr(handler, "execute", None)

    if not isinstance(job_type, str):
        raise InvalidJobHandlerError("A job handler must expose a string job_type.")

    _normalize_job_type(job_type)

    if (
        isinstance(supported_payload_version, bool)
        or not isinstance(supported_payload_version, int)
        or supported_payload_version < 1
    ):
        raise InvalidJobHandlerError(
            "A job handler must expose a positive integer supported_payload_version."
        )

    if not callable(execute):
        raise InvalidJobHandlerError("A job handler must expose a callable execute method.")

    return cast(JobHandler, handler)


def _normalize_job_type(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidJobHandlerError("job_type must be a string.")

    normalized = value.strip()

    if not normalized:
        raise InvalidJobHandlerError("job_type must not be empty.")

    if len(normalized) > _MAX_JOB_TYPE_LENGTH:
        raise InvalidJobHandlerError(
            f"job_type must contain at most {_MAX_JOB_TYPE_LENGTH} characters."
        )

    return normalized


__all__ = ["JobHandlerRegistry"]
