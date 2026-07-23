from dataclasses import dataclass

import pytest

from clinicops.jobs.contracts import ClaimedBackgroundJob
from clinicops.jobs.runtime.exceptions import (
    DuplicateJobHandlerError,
    InvalidJobHandlerError,
    UnknownJobTypeError,
)
from clinicops.jobs.runtime.registry import JobHandlerRegistry


@dataclass
class RecordingJobHandler:
    job_type: str
    supported_payload_version: int = 1
    execution_count: int = 0

    def execute(
        self,
        job: ClaimedBackgroundJob,
    ) -> None:
        self.execution_count += 1


class MissingExecuteHandler:
    job_type = "billing.webhook.process"
    supported_payload_version = 1


def test_registry_registers_and_resolves_handler() -> None:
    handler = RecordingJobHandler(job_type="billing.webhook.process")
    registry = JobHandlerRegistry()

    registry.register(handler)

    resolved = registry.resolve("billing.webhook.process")

    assert resolved is handler
    assert registry.registered_job_types == ("billing.webhook.process",)


def test_registry_accepts_initial_handlers() -> None:
    webhook_handler = RecordingJobHandler(job_type="billing.webhook.process")
    reconciliation_handler = RecordingJobHandler(job_type="billing.subscription.reconcile")

    registry = JobHandlerRegistry(
        [
            webhook_handler,
            reconciliation_handler,
        ]
    )

    assert registry.resolve("billing.webhook.process") is webhook_handler
    assert registry.resolve("billing.subscription.reconcile") is reconciliation_handler
    assert registry.registered_job_types == (
        "billing.subscription.reconcile",
        "billing.webhook.process",
    )


def test_registry_rejects_duplicate_job_type() -> None:
    registry = JobHandlerRegistry([RecordingJobHandler(job_type="billing.webhook.process")])

    with pytest.raises(DuplicateJobHandlerError):
        registry.register(RecordingJobHandler(job_type="billing.webhook.process"))


def test_registry_rejects_unknown_job_type() -> None:
    registry = JobHandlerRegistry()

    with pytest.raises(UnknownJobTypeError) as exception_info:
        registry.resolve("billing.webhook.process")

    assert exception_info.value.job_type == "billing.webhook.process"
    assert exception_info.value.retryable is False
    assert exception_info.value.error_code == "unknown_job_type"


@pytest.mark.parametrize(
    "job_type",
    [
        "",
        " ",
        "x" * 101,
    ],
)
def test_registry_rejects_invalid_handler_job_type(
    job_type: str,
) -> None:
    registry = JobHandlerRegistry()

    with pytest.raises(InvalidJobHandlerError):
        registry.register(RecordingJobHandler(job_type=job_type))


@pytest.mark.parametrize(
    "supported_payload_version",
    [
        0,
        -1,
        True,
    ],
)
def test_registry_rejects_invalid_payload_version(
    supported_payload_version: int,
) -> None:
    registry = JobHandlerRegistry()

    with pytest.raises(InvalidJobHandlerError):
        registry.register(
            RecordingJobHandler(
                job_type="billing.webhook.process",
                supported_payload_version=(supported_payload_version),
            )
        )


def test_registry_rejects_object_without_execute() -> None:
    registry = JobHandlerRegistry()

    with pytest.raises(InvalidJobHandlerError):
        registry.register(
            MissingExecuteHandler()  # type: ignore[arg-type]
        )


def test_registry_rejects_arbitrary_object() -> None:
    registry = JobHandlerRegistry()

    with pytest.raises(InvalidJobHandlerError):
        registry.register(object())  # type: ignore[arg-type]


def test_registry_normalizes_lookup_job_type() -> None:
    handler = RecordingJobHandler(job_type="billing.webhook.process")
    registry = JobHandlerRegistry([handler])

    resolved = registry.resolve("  billing.webhook.process  ")

    assert resolved is handler


def test_registry_does_not_resolve_unregistered_module_path() -> None:
    registry = JobHandlerRegistry()

    with pytest.raises(UnknownJobTypeError):
        registry.resolve("clinicops.billing.jobs.process_billing_webhook_event")
