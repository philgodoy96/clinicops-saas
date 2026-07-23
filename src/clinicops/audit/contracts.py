from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from clinicops.audit.enums import AuditActorType, AuditSource
from clinicops.audit.exceptions import (
    AuditLogInvalidActorError,
)

type JSONScalar = None | bool | int | float | str
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

_MAX_ACTOR_ROLE_LENGTH = 50


@dataclass(frozen=True, slots=True)
class AuditActor:
    actor_type: AuditActorType
    user_id: UUID | None
    role: str | None

    def __post_init__(self) -> None:
        if not isinstance(
            self.actor_type,
            AuditActorType,
        ):
            raise AuditLogInvalidActorError("actor_type must be a supported AuditActorType.")

        normalized_role = _normalize_role(self.role)

        if self.actor_type is AuditActorType.USER:
            if not isinstance(self.user_id, UUID):
                raise AuditLogInvalidActorError("A user audit actor must include a valid user_id.")

            object.__setattr__(
                self,
                "role",
                normalized_role,
            )
            return

        if self.user_id is not None:
            raise AuditLogInvalidActorError("A system audit actor must not include a user_id.")

        if normalized_role is not None:
            raise AuditLogInvalidActorError("A system audit actor must not include a role.")

        object.__setattr__(
            self,
            "role",
            None,
        )

    @classmethod
    def user(
        cls,
        user_id: UUID,
        *,
        role: str | None = None,
    ) -> "AuditActor":
        return cls(
            actor_type=AuditActorType.USER,
            user_id=user_id,
            role=role,
        )

    @classmethod
    def system(cls) -> "AuditActor":
        return cls(
            actor_type=AuditActorType.SYSTEM,
            user_id=None,
            role=None,
        )


@dataclass(frozen=True, slots=True)
class RecordAuditLogCommand:
    tenant_id: UUID
    actor: AuditActor
    source: AuditSource
    action: str
    resource_type: str
    resource_id: str
    correlation_id: str
    metadata_version: int = 1
    metadata: JSONObject = field(default_factory=dict)
    idempotency_key: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecordedAuditLog:
    audit_log_id: UUID
    created: bool
    recorded_at: datetime


def _normalize_role(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise AuditLogInvalidActorError("An audit actor role must be a string.")

    normalized = value.strip()

    if not normalized:
        raise AuditLogInvalidActorError("An audit actor role must not be empty.")

    if len(normalized) > _MAX_ACTOR_ROLE_LENGTH:
        raise AuditLogInvalidActorError(
            f"An audit actor role must contain at most {_MAX_ACTOR_ROLE_LENGTH} characters."
        )

    return normalized


__all__ = [
    "AuditActor",
    "JSONObject",
    "JSONScalar",
    "JSONValue",
    "RecordAuditLogCommand",
    "RecordedAuditLog",
]
