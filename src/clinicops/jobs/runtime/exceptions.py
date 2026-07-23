class JobRuntimeError(Exception):
    code = "job_runtime_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class InvalidJobHandlerError(JobRuntimeError):
    code = "invalid_job_handler"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class DuplicateJobHandlerError(JobRuntimeError):
    code = "duplicate_job_handler"

    def __init__(self, job_type: str) -> None:
        self.job_type = job_type
        super().__init__(
            f"A background job handler is already registered for job type '{job_type}'."
        )


class JobExecutionError(JobRuntimeError):
    retryable: bool

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
    ) -> None:
        self.error_code = error_code
        super().__init__(message)


class RetryableJobExecutionError(JobExecutionError):
    code = "retryable_job_execution_error"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "retryable_job_execution_error",
    ) -> None:
        super().__init__(
            message,
            error_code=error_code,
        )


class TerminalJobExecutionError(JobExecutionError):
    code = "terminal_job_execution_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "terminal_job_execution_error",
    ) -> None:
        super().__init__(
            message,
            error_code=error_code,
        )


class UnknownJobTypeError(TerminalJobExecutionError):
    code = "unknown_job_type"

    def __init__(self, job_type: str) -> None:
        self.job_type = job_type
        super().__init__(
            f"No background job handler is registered for job type '{job_type}'.",
            error_code=self.code,
        )


class UnsupportedJobPayloadVersionError(TerminalJobExecutionError):
    code = "unsupported_job_payload_version"

    def __init__(
        self,
        *,
        job_type: str,
        received_version: int,
        supported_version: int,
    ) -> None:
        self.job_type = job_type
        self.received_version = received_version
        self.supported_version = supported_version
        super().__init__(
            f"Background job type '{job_type}' received "
            f"payload version {received_version}, but the "
            f"registered handler supports version "
            f"{supported_version}.",
            error_code=self.code,
        )


class InvalidJobPayloadError(TerminalJobExecutionError):
    code = "invalid_job_payload"

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            error_code=self.code,
        )


__all__ = [
    "DuplicateJobHandlerError",
    "InvalidJobHandlerError",
    "InvalidJobPayloadError",
    "JobExecutionError",
    "JobRuntimeError",
    "RetryableJobExecutionError",
    "TerminalJobExecutionError",
    "UnknownJobTypeError",
    "UnsupportedJobPayloadVersionError",
]
