from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.audit.contracts import (
    AuditActor,
    JSONObject,
    RecordAuditLogCommand,
)
from clinicops.audit.enums import AuditSource
from clinicops.audit.exceptions import (
    AuditLogIdempotencyConflictError,
    AuditLogInvalidConfigurationError,
    AuditLogInvalidMetadataError,
)
from clinicops.audit.models import AuditLogEntry
from clinicops.audit.repositories.audit_log_repository import (
    AuditLogRepository,
)
from clinicops.audit.services.record_audit_log import (
    RecordAuditLogService,
)

_RECORDED_AT = datetime(
    2026,
    7,
    23,
    12,
    0,
    tzinfo=UTC,
)


class RecordingAuditLogRepository:
    def __init__(
        self,
        *,
        existing: AuditLogEntry | None = None,
    ) -> None:
        self.existing = existing
        self.candidates: list[AuditLogEntry] = []
        self.flush_count = 0

    def insert_or_get_by_idempotency_key(
        self,
        entry: AuditLogEntry,
    ) -> tuple[AuditLogEntry, bool]:
        self.candidates.append(entry)

        if self.existing is not None:
            return self.existing, False

        entry.recorded_at = _RECORDED_AT
        return entry, True

    def flush(self) -> None:
        self.flush_count += 1


def _repository(
    *,
    existing: AuditLogEntry | None = None,
) -> tuple[
    AuditLogRepository,
    RecordingAuditLogRepository,
]:
    recording = RecordingAuditLogRepository(existing=existing)

    return (
        cast(AuditLogRepository, recording),
        recording,
    )


def _command(
    *,
    tenant_id: UUID | None = None,
    action: str = "membership.role_changed",
    resource_type: str = "membership",
    resource_id: str | None = None,
    correlation_id: str = "correlation-123",
    idempotency_key: str | None = ("membership-role-change:123"),
    request_id: str | None = "request-123",
    metadata_version: int = 1,
    metadata: JSONObject | None = None,
) -> RecordAuditLogCommand:
    return RecordAuditLogCommand(
        tenant_id=(tenant_id if tenant_id is not None else uuid4()),
        actor=AuditActor.user(
            uuid4(),
            role="ADMIN",
        ),
        source=AuditSource.HTTP,
        action=action,
        resource_type=resource_type,
        resource_id=(resource_id if resource_id is not None else str(uuid4())),
        correlation_id=correlation_id,
        metadata_version=metadata_version,
        metadata=(
            metadata
            if metadata is not None
            else {
                "previous_role": "STAFF",
                "new_role": "ADMIN",
            }
        ),
        idempotency_key=idempotency_key,
        request_id=request_id,
    )


def _persisted_entry(
    command: RecordAuditLogCommand,
    *,
    action: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> AuditLogEntry:
    return AuditLogEntry(
        id=uuid4(),
        tenant_id=command.tenant_id,
        actor_type=command.actor.actor_type.value,
        actor_user_id=command.actor.user_id,
        actor_role=command.actor.role,
        source=command.source.value,
        action=(action if action is not None else command.action.strip()),
        resource_type=command.resource_type.strip(),
        resource_id=command.resource_id.strip(),
        metadata_version=command.metadata_version,
        event_metadata={
            "new_role": "ADMIN",
            "previous_role": "STAFF",
        },
        idempotency_key=command.idempotency_key,
        request_id=(request_id if request_id is not None else command.request_id),
        correlation_id=(correlation_id if correlation_id is not None else command.correlation_id),
        recorded_at=_RECORDED_AT,
    )


def test_service_records_normalized_audit_entry() -> None:
    repository, recording = _repository()
    service = RecordAuditLogService(repository)
    tenant_id = uuid4()
    resource_id = str(uuid4())

    result = service.execute(
        _command(
            tenant_id=tenant_id,
            action="  membership.role_changed  ",
            resource_type="  membership  ",
            resource_id=f"  {resource_id}  ",
            correlation_id="  correlation-123  ",
            idempotency_key=("  membership-role-change:123  "),
            request_id="  request-123  ",
            metadata={
                "previous_role": "STAFF",
                "new_role": "ADMIN",
            },
        )
    )

    assert result.created is True
    assert result.recorded_at == _RECORDED_AT
    assert recording.flush_count == 1
    assert len(recording.candidates) == 1

    candidate = recording.candidates[0]

    assert result.audit_log_id == candidate.id
    assert candidate.tenant_id == tenant_id
    assert candidate.actor_type == "user"
    assert candidate.actor_role == "ADMIN"
    assert candidate.source == "http"
    assert candidate.action == "membership.role_changed"
    assert candidate.resource_type == "membership"
    assert candidate.resource_id == resource_id
    assert candidate.correlation_id == ("correlation-123")
    assert candidate.request_id == "request-123"
    assert candidate.idempotency_key == ("membership-role-change:123")
    assert list(candidate.event_metadata) == [
        "new_role",
        "previous_role",
    ]


def test_equivalent_replay_reuses_existing_entry() -> None:
    command = _command(
        request_id="request-replay",
        correlation_id="correlation-replay",
    )
    existing = _persisted_entry(
        command,
        request_id="request-original",
        correlation_id="correlation-original",
    )
    repository, recording = _repository(existing=existing)

    result = RecordAuditLogService(repository).execute(command)

    assert result.audit_log_id == existing.id
    assert result.created is False
    assert result.recorded_at == _RECORDED_AT
    assert recording.flush_count == 1

    assert existing.request_id == "request-original"
    assert existing.correlation_id == "correlation-original"


def test_replay_transport_identifiers_are_not_semantic() -> None:
    command = _command(
        request_id="request-replay",
        correlation_id="correlation-replay",
    )
    existing = _persisted_entry(
        command,
        request_id="request-original",
        correlation_id="correlation-original",
    )
    repository, _ = _repository(existing=existing)

    result = RecordAuditLogService(repository).execute(command)

    assert result.created is False


def test_conflicting_replay_raises_semantic_conflict() -> None:
    command = _command()
    existing = _persisted_entry(
        command,
        action="membership.removed",
    )
    repository, recording = _repository(existing=existing)

    with pytest.raises(
        AuditLogIdempotencyConflictError,
    ) as exc_info:
        RecordAuditLogService(repository).execute(command)

    assert exc_info.value.idempotency_key == (command.idempotency_key)
    assert recording.flush_count == 1


def test_system_actor_is_mapped_without_user_fields() -> None:
    repository, recording = _repository()
    service = RecordAuditLogService(repository)

    command = RecordAuditLogCommand(
        tenant_id=uuid4(),
        actor=AuditActor.system(),
        source=AuditSource.WORKER,
        action="billing.webhook.processed",
        resource_type="billing_webhook_event",
        resource_id=str(uuid4()),
        correlation_id="correlation-worker",
        metadata={},
    )

    service.execute(command)

    candidate = recording.candidates[0]

    assert candidate.actor_type == "system"
    assert candidate.actor_user_id is None
    assert candidate.actor_role is None
    assert candidate.source == "worker"


@pytest.mark.parametrize(
    ("field_name", "field_value", "message"),
    [
        ("action", " ", "action must not be empty"),
        (
            "resource_type",
            "",
            "resource_type must not be empty",
        ),
        (
            "resource_id",
            "\t",
            "resource_id must not be empty",
        ),
        (
            "correlation_id",
            "\n",
            "correlation_id must not be empty",
        ),
        (
            "idempotency_key",
            " ",
            "idempotency_key must not be empty",
        ),
        (
            "request_id",
            "",
            "request_id must not be empty",
        ),
    ],
)
def test_service_rejects_blank_fields_before_persistence(
    field_name: str,
    field_value: str,
    message: str,
) -> None:
    repository, recording = _repository()

    values = {
        "action": "membership.role_changed",
        "resource_type": "membership",
        "resource_id": str(uuid4()),
        "correlation_id": "correlation-123",
        "idempotency_key": "audit-key",
        "request_id": "request-123",
    }
    values[field_name] = field_value

    command = _command(
        action=values["action"],
        resource_type=values["resource_type"],
        resource_id=values["resource_id"],
        correlation_id=values["correlation_id"],
        idempotency_key=values["idempotency_key"],
        request_id=values["request_id"],
    )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match=message,
    ):
        RecordAuditLogService(repository).execute(command)

    assert recording.candidates == []
    assert recording.flush_count == 0


@pytest.mark.parametrize(
    "metadata_version",
    [
        0,
        -1,
        True,
    ],
)
def test_service_rejects_invalid_metadata_version(
    metadata_version: int,
) -> None:
    repository, recording = _repository()

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="metadata_version",
    ):
        RecordAuditLogService(repository).execute(
            _command(
                metadata_version=metadata_version,
            )
        )

    assert recording.candidates == []


def test_service_rejects_unsafe_metadata_before_persistence() -> None:
    repository, recording = _repository()

    with pytest.raises(
        AuditLogInvalidMetadataError,
    ):
        RecordAuditLogService(repository).execute(
            _command(
                metadata=cast(
                    JSONObject,
                    {
                        "unsafe": uuid4(),
                    },
                )
            )
        )

    assert recording.candidates == []
    assert recording.flush_count == 0
