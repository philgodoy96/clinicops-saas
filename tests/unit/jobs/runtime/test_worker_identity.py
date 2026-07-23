import pytest

from clinicops.jobs.exceptions import (
    BackgroundJobInvalidConfigurationError,
)
from clinicops.jobs.runtime.identity import resolve_worker_id


def test_resolve_worker_id_preserves_explicit_value() -> None:
    worker_id = resolve_worker_id("  worker-production-1  ")

    assert worker_id == "worker-production-1"


def test_resolve_worker_id_generates_process_identity() -> None:
    worker_id = resolve_worker_id(
        hostname_provider=lambda: "clinicops-worker",
        process_id_provider=lambda: 4821,
        suffix_provider=lambda: "a1b2c3d4",
    )

    assert worker_id == ("clinicops-worker:4821:a1b2c3d4")


def test_generated_worker_id_normalizes_colons() -> None:
    worker_id = resolve_worker_id(
        hostname_provider=lambda: "worker:node",
        process_id_provider=lambda: 7,
        suffix_provider=lambda: "suffix:value",
    )

    assert worker_id == "worker-node:7:suffix-value"


def test_generated_worker_id_truncates_long_hostname() -> None:
    worker_id = resolve_worker_id(
        hostname_provider=lambda: "h" * 300,
        process_id_provider=lambda: 42,
        suffix_provider=lambda: "suffix",
    )

    assert len(worker_id) == 255
    assert worker_id.endswith(":42:suffix")


@pytest.mark.parametrize(
    "worker_id",
    [
        "",
        " ",
        "x" * 256,
    ],
)
def test_resolve_worker_id_rejects_invalid_explicit_value(
    worker_id: str,
) -> None:
    with pytest.raises(BackgroundJobInvalidConfigurationError):
        resolve_worker_id(worker_id)


@pytest.mark.parametrize(
    "process_id",
    [
        0,
        -1,
        True,
    ],
)
def test_resolve_worker_id_rejects_invalid_process_id(
    process_id: int,
) -> None:
    with pytest.raises(BackgroundJobInvalidConfigurationError):
        resolve_worker_id(
            hostname_provider=lambda: "worker",
            process_id_provider=lambda: process_id,
            suffix_provider=lambda: "suffix",
        )


@pytest.mark.parametrize(
    ("hostname", "suffix"),
    [
        ("", "suffix"),
        ("worker", ""),
        (" ", "suffix"),
        ("worker", " "),
    ],
)
def test_resolve_worker_id_rejects_empty_components(
    hostname: str,
    suffix: str,
) -> None:
    with pytest.raises(BackgroundJobInvalidConfigurationError):
        resolve_worker_id(
            hostname_provider=lambda: hostname,
            process_id_provider=lambda: 1,
            suffix_provider=lambda: suffix,
        )
