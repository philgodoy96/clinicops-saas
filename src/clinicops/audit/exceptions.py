from clinicops.core.exceptions import ApplicationError


class AuditLogError(ApplicationError):
    code = "audit_log_error"
    public_message = "The audit log operation could not be completed."

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class AuditLogInvalidActorError(AuditLogError):
    code = "audit_log_invalid_actor"
    public_message = "The audit actor is invalid."


class AuditLogInvalidMetadataError(AuditLogError):
    code = "audit_log_invalid_metadata"
    public_message = "The audit metadata is invalid."


class AuditLogInvalidConfigurationError(AuditLogError):
    code = "audit_log_invalid_configuration"
    public_message = "The audit log request is invalid."


class AuditLogIdempotencyConflictError(AuditLogError):
    code = "audit_log_idempotency_conflict"
    public_message = "The audit log idempotency key conflicts with an existing entry."

    def __init__(
        self,
        idempotency_key: str,
    ) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            "The audit log idempotency key is already associated with a different historical event."
        )


__all__ = [
    "AuditLogError",
    "AuditLogIdempotencyConflictError",
    "AuditLogInvalidActorError",
    "AuditLogInvalidConfigurationError",
    "AuditLogInvalidMetadataError",
]
