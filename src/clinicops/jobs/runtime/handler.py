from typing import Protocol

from clinicops.jobs.contracts import ClaimedBackgroundJob


class JobHandler(Protocol):
    @property
    def job_type(self) -> str:
        """Return the stable job type handled by this adapter."""

    @property
    def supported_payload_version(self) -> int:
        """Return the payload version supported by this handler."""

    def execute(
        self,
        job: ClaimedBackgroundJob,
    ) -> None:
        """Execute the claimed background job."""


__all__ = ["JobHandler"]
