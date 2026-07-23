from dataclasses import FrozenInstanceError
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.audit.context import (
    AuditRecordingContext,
)
from clinicops.audit.enums import (
    AuditActorType,
    AuditSource,
)
from clinicops.audit.exceptions import (
    AuditLogInvalidActorError,
    AuditLogInvalidConfigurationError,
)


def test_http_user_context_preserves_attribution() -> None:
    user_id = uuid4()

    context = AuditRecordingContext.http_user(
        user_id=user_id,
        role="ADMIN",
        request_id="request-123",
        correlation_id="correlation-123",
    )

    assert context.actor.actor_type is AuditActorType.USER
    assert context.actor.user_id == user_id
    assert context.actor.role == "ADMIN"
    assert context.source is AuditSource.HTTP
    assert context.request_id == "request-123"
    assert context.correlation_id == "correlation-123"


def test_http_context_normalizes_identifiers() -> None:
    context = AuditRecordingContext.http_user(
        user_id=uuid4(),
        role="OWNER",
        request_id="  request-123  ",
        correlation_id="  correlation-123  ",
    )

    assert context.request_id == "request-123"
    assert context.correlation_id == "correlation-123"


def test_worker_system_context_preserves_origin() -> None:
    context = AuditRecordingContext.worker_system(
        request_id="origin-request-123",
        correlation_id="job-correlation-123",
    )

    assert context.actor.actor_type is AuditActorType.SYSTEM
    assert context.actor.user_id is None
    assert context.actor.role is None
    assert context.source is AuditSource.WORKER
    assert context.request_id == "origin-request-123"
    assert context.correlation_id == "job-correlation-123"


def test_worker_context_allows_missing_request_id() -> None:
    context = AuditRecordingContext.worker_system(
        correlation_id="job-correlation-123",
    )

    assert context.request_id is None


def test_context_is_immutable() -> None:
    context = AuditRecordingContext.worker_system(
        correlation_id="correlation-123",
    )

    with pytest.raises(FrozenInstanceError):
        context.request_id = (  # type: ignore[misc]
            "request-456"
        )


def test_http_context_requires_uuid_user_id() -> None:
    with pytest.raises(
        AuditLogInvalidActorError,
        match="valid user_id",
    ):
        AuditRecordingContext.http_user(
            user_id=cast(UUID, "not-a-uuid"),
            role="ADMIN",
            request_id="request-123",
            correlation_id="correlation-123",
        )


@pytest.mark.parametrize(
    "correlation_id",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def test_context_rejects_blank_correlation_id(
    correlation_id: str,
) -> None:
    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="correlation_id must not be empty",
    ):
        AuditRecordingContext.worker_system(
            correlation_id=correlation_id,
        )


@pytest.mark.parametrize(
    "request_id",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def test_context_rejects_blank_request_id(
    request_id: str,
) -> None:
    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="request_id must not be empty",
    ):
        AuditRecordingContext.worker_system(
            request_id=request_id,
            correlation_id="correlation-123",
        )


def test_context_rejects_oversized_identifiers() -> None:
    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="correlation_id must contain at most",
    ):
        AuditRecordingContext.worker_system(
            correlation_id="x" * 256,
        )

    with pytest.raises(
        AuditLogInvalidConfigurationError,
        match="request_id must contain at most",
    ):
        AuditRecordingContext.worker_system(
            request_id="x" * 256,
            correlation_id="correlation-123",
        )
