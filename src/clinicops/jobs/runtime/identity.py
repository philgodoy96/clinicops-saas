import os
import socket
from collections.abc import Callable
from uuid import uuid4

from clinicops.jobs.exceptions import (
    BackgroundJobInvalidConfigurationError,
)

_MAX_WORKER_ID_LENGTH = 255

HostnameProvider = Callable[[], str]
ProcessIdProvider = Callable[[], int]
SuffixProvider = Callable[[], str]


def resolve_worker_id(
    configured_worker_id: str | None = None,
    *,
    hostname_provider: HostnameProvider = socket.gethostname,
    process_id_provider: ProcessIdProvider = os.getpid,
    suffix_provider: SuffixProvider | None = None,
) -> str:
    if configured_worker_id is not None:
        return _normalize_worker_id(configured_worker_id)

    hostname = _normalize_generated_component(
        hostname_provider(),
        field_name="hostname",
    )
    process_id = process_id_provider()
    suffix = _normalize_generated_component(
        (suffix_provider() if suffix_provider is not None else uuid4().hex[:8]),
        field_name="worker suffix",
    )

    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id < 1:
        raise BackgroundJobInvalidConfigurationError(
            "The worker process ID must be a positive integer."
        )

    trailing_content = f":{process_id}:{suffix}"
    maximum_hostname_length = _MAX_WORKER_ID_LENGTH - len(trailing_content)

    if maximum_hostname_length < 1:
        raise BackgroundJobInvalidConfigurationError(
            "The generated worker identity exceeds the supported length."
        )

    generated_worker_id = f"{hostname[:maximum_hostname_length]}{trailing_content}"

    return _normalize_worker_id(generated_worker_id)


def _normalize_worker_id(value: str) -> str:
    if not isinstance(value, str):
        raise BackgroundJobInvalidConfigurationError("worker_id must be a string.")

    normalized = value.strip()

    if not normalized:
        raise BackgroundJobInvalidConfigurationError("worker_id must not be empty.")

    if len(normalized) > _MAX_WORKER_ID_LENGTH:
        raise BackgroundJobInvalidConfigurationError(
            "worker_id must contain at most 255 characters."
        )

    return normalized


def _normalize_generated_component(
    value: str,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise BackgroundJobInvalidConfigurationError(
            f"The generated {field_name} must be a string."
        )

    normalized = value.strip().replace(":", "-")

    if not normalized:
        raise BackgroundJobInvalidConfigurationError(
            f"The generated {field_name} must not be empty."
        )

    return normalized


__all__ = [
    "HostnameProvider",
    "ProcessIdProvider",
    "SuffixProvider",
    "resolve_worker_id",
]
