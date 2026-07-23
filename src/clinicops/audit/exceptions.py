class AuditLogError(Exception):
    code = "audit_log_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class AuditLogInvalidActorError(AuditLogError):
    code = "audit_log_invalid_actor"


class AuditLogInvalidMetadataError(AuditLogError):
    code = "audit_log_invalid_metadata"


class AuditLogInvalidConfigurationError(AuditLogError):
    code = "audit_log_invalid_configuration"


class AuditLogIdempotencyConflictError(AuditLogError):
    code = "audit_log_idempotency_conflict"

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
