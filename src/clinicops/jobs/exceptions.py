from uuid import UUID


class BackgroundJobError(Exception):
    code = "background_job_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class BackgroundJobNotFoundError(BackgroundJobError):
    code = "background_job_not_found"

    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(f"Background job '{job_id}' was not found.")


class BackgroundJobIdempotencyConflictError(BackgroundJobError):
    code = "background_job_idempotency_conflict"

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            "The background job idempotency key is already associated with different semantic work."
        )


class BackgroundJobClaimOwnershipError(BackgroundJobError):
    code = "background_job_claim_ownership_mismatch"

    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(f"Background job '{job_id}' is not owned by the provided worker claim.")


class BackgroundJobInvalidTransitionError(BackgroundJobError):
    code = "background_job_invalid_transition"

    def __init__(
        self,
        *,
        job_id: UUID,
        current_status: str,
        target_status: str,
    ) -> None:
        self.job_id = job_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Background job '{job_id}' cannot transition from "
            f"'{current_status}' to '{target_status}'."
        )


class BackgroundJobInvalidPayloadError(BackgroundJobError):
    code = "background_job_invalid_payload"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class BackgroundJobInvalidConfigurationError(BackgroundJobError):
    code = "background_job_invalid_configuration"

    def __init__(self, message: str) -> None:
        super().__init__(message)


__all__ = [
    "BackgroundJobClaimOwnershipError",
    "BackgroundJobError",
    "BackgroundJobIdempotencyConflictError",
    "BackgroundJobInvalidConfigurationError",
    "BackgroundJobInvalidPayloadError",
    "BackgroundJobInvalidTransitionError",
    "BackgroundJobNotFoundError",
]
