from dataclasses import dataclass
from uuid import UUID

from clinicops.audit.contracts import AuditActor
from clinicops.audit.enums import AuditSource
from clinicops.audit.exceptions import (
    AuditLogInvalidConfigurationError,
)

_MAX_REQUEST_ID_LENGTH = 255
_MAX_CORRELATION_ID_LENGTH = 255


@dataclass(frozen=True, slots=True)
class AuditRecordingContext:
    actor: AuditActor
    source: AuditSource
    correlation_id: str
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.actor, AuditActor):
            raise AuditLogInvalidConfigurationError("actor must be an AuditActor.")

        if not isinstance(self.source, AuditSource):
            raise AuditLogInvalidConfigurationError("source must be a supported AuditSource.")

        object.__setattr__(
            self,
            "correlation_id",
            _normalize_required_identifier(
                self.correlation_id,
                field_name="correlation_id",
                maximum_length=(_MAX_CORRELATION_ID_LENGTH),
            ),
        )

        object.__setattr__(
            self,
            "request_id",
            _normalize_optional_identifier(
                self.request_id,
                field_name="request_id",
                maximum_length=_MAX_REQUEST_ID_LENGTH,
            ),
        )

    @classmethod
    def http_user(
        cls,
        *,
        user_id: UUID,
        role: str,
        request_id: str,
        correlation_id: str,
    ) -> "AuditRecordingContext":
        return cls(
            actor=AuditActor.user(
                user_id,
                role=role,
            ),
            source=AuditSource.HTTP,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    @classmethod
    def worker_system(
        cls,
        *,
        correlation_id: str,
        request_id: str | None = None,
    ) -> "AuditRecordingContext":
        return cls(
            actor=AuditActor.system(),
            source=AuditSource.WORKER,
            request_id=request_id,
            correlation_id=correlation_id,
        )


def _normalize_required_identifier(
    value: object,
    *,
    field_name: str,
    maximum_length: int,
) -> str:
    if not isinstance(value, str):
        raise AuditLogInvalidConfigurationError(f"{field_name} must be a string.")

    normalized = value.strip()

    if not normalized:
        raise AuditLogInvalidConfigurationError(f"{field_name} must not be empty.")

    if len(normalized) > maximum_length:
        raise AuditLogInvalidConfigurationError(
            f"{field_name} must contain at most {maximum_length} characters."
        )

    return normalized


def _normalize_optional_identifier(
    value: object,
    *,
    field_name: str,
    maximum_length: int,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise AuditLogInvalidConfigurationError(f"{field_name} must be a string when provided.")

    normalized = value.strip()

    if not normalized:
        raise AuditLogInvalidConfigurationError(f"{field_name} must not be empty when provided.")

    if len(normalized) > maximum_length:
        raise AuditLogInvalidConfigurationError(
            f"{field_name} must contain at most {maximum_length} characters."
        )

    return normalized


__all__ = ["AuditRecordingContext"]
