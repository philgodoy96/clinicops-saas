from uuid import UUID, uuid4

from clinicops.audit.contracts import (
    AuditActor,
    RecordAuditLogCommand,
    RecordedAuditLog,
)
from clinicops.audit.enums import AuditSource
from clinicops.audit.exceptions import (
    AuditLogIdempotencyConflictError,
    AuditLogInvalidConfigurationError,
)
from clinicops.audit.metadata import (
    normalize_audit_metadata,
)
from clinicops.audit.models import AuditLogEntry
from clinicops.audit.repositories.audit_log_repository import (
    AuditLogRepository,
)

_MAX_ACTION_LENGTH = 100
_MAX_RESOURCE_TYPE_LENGTH = 100
_MAX_RESOURCE_ID_LENGTH = 255
_MAX_IDEMPOTENCY_KEY_LENGTH = 255
_MAX_REQUEST_ID_LENGTH = 255
_MAX_CORRELATION_ID_LENGTH = 255


class RecordAuditLogService:
    def __init__(
        self,
        repository: AuditLogRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        normalized = _normalize_command(command)

        candidate = AuditLogEntry(
            id=uuid4(),
            tenant_id=normalized.tenant_id,
            actor_type=(normalized.actor.actor_type.value),
            actor_user_id=normalized.actor.user_id,
            actor_role=normalized.actor.role,
            source=normalized.source.value,
            action=normalized.action,
            resource_type=normalized.resource_type,
            resource_id=normalized.resource_id,
            metadata_version=(normalized.metadata_version),
            event_metadata=normalized.metadata,
            idempotency_key=(normalized.idempotency_key),
            request_id=normalized.request_id,
            correlation_id=normalized.correlation_id,
        )

        persisted, created = self._repository.insert_or_get_by_idempotency_key(candidate)

        self._repository.flush()

        if not created and not _is_semantically_equivalent(
            persisted,
            candidate,
        ):
            if candidate.idempotency_key is None:
                raise AuditLogInvalidConfigurationError(
                    "An idempotency conflict requires an idempotency key."
                )

            raise AuditLogIdempotencyConflictError(candidate.idempotency_key)

        if persisted.recorded_at is None:
            raise AuditLogInvalidConfigurationError(
                "The audit log entry does not have a database-generated recorded_at value."
            )

        return RecordedAuditLog(
            audit_log_id=persisted.id,
            created=created,
            recorded_at=persisted.recorded_at,
        )


def _normalize_command(
    command: RecordAuditLogCommand,
) -> RecordAuditLogCommand:
    if not isinstance(
        command,
        RecordAuditLogCommand,
    ):
        raise AuditLogInvalidConfigurationError("command must be a RecordAuditLogCommand.")

    if not isinstance(command.tenant_id, UUID):
        raise AuditLogInvalidConfigurationError("tenant_id must be a UUID.")

    if not isinstance(command.actor, AuditActor):
        raise AuditLogInvalidConfigurationError("actor must be an AuditActor.")

    if not isinstance(command.source, AuditSource):
        raise AuditLogInvalidConfigurationError("source must be a supported AuditSource.")

    if type(command.metadata_version) is not int or command.metadata_version < 1:
        raise AuditLogInvalidConfigurationError(
            "metadata_version must be an integer greater than or equal to 1."
        )

    return RecordAuditLogCommand(
        tenant_id=command.tenant_id,
        actor=command.actor,
        source=command.source,
        action=_normalize_required_string(
            command.action,
            field_name="action",
            maximum_length=_MAX_ACTION_LENGTH,
        ),
        resource_type=_normalize_required_string(
            command.resource_type,
            field_name="resource_type",
            maximum_length=(_MAX_RESOURCE_TYPE_LENGTH),
        ),
        resource_id=_normalize_required_string(
            command.resource_id,
            field_name="resource_id",
            maximum_length=_MAX_RESOURCE_ID_LENGTH,
        ),
        correlation_id=_normalize_required_string(
            command.correlation_id,
            field_name="correlation_id",
            maximum_length=(_MAX_CORRELATION_ID_LENGTH),
        ),
        metadata_version=command.metadata_version,
        metadata=normalize_audit_metadata(command.metadata),
        idempotency_key=_normalize_optional_string(
            command.idempotency_key,
            field_name="idempotency_key",
            maximum_length=(_MAX_IDEMPOTENCY_KEY_LENGTH),
        ),
        request_id=_normalize_optional_string(
            command.request_id,
            field_name="request_id",
            maximum_length=_MAX_REQUEST_ID_LENGTH,
        ),
    )


def _normalize_required_string(
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


def _normalize_optional_string(
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


def _is_semantically_equivalent(
    persisted: AuditLogEntry,
    candidate: AuditLogEntry,
) -> bool:
    return (
        persisted.tenant_id == candidate.tenant_id
        and persisted.actor_type == candidate.actor_type
        and persisted.actor_user_id == candidate.actor_user_id
        and persisted.actor_role == candidate.actor_role
        and persisted.source == candidate.source
        and persisted.action == candidate.action
        and persisted.resource_type == candidate.resource_type
        and persisted.resource_id == candidate.resource_id
        and persisted.metadata_version == candidate.metadata_version
        and persisted.event_metadata == candidate.event_metadata
    )


__all__ = ["RecordAuditLogService"]
