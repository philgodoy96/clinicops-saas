from enum import StrEnum


class BackgroundJobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    DEAD_LETTERED = "dead_lettered"


class BackgroundJobFailureKind(StrEnum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"
