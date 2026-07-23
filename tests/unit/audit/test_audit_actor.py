from dataclasses import FrozenInstanceError
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.audit.contracts import AuditActor
from clinicops.audit.enums import AuditActorType
from clinicops.audit.exceptions import (
    AuditLogInvalidActorError,
)


def test_user_actor_preserves_user_identity() -> None:
    user_id = uuid4()

    actor = AuditActor.user(
        user_id,
        role="OWNER",
    )

    assert actor.actor_type is AuditActorType.USER
    assert actor.user_id == user_id
    assert actor.role == "OWNER"


def test_user_actor_allows_missing_role() -> None:
    user_id = uuid4()

    actor = AuditActor.user(user_id)

    assert actor.actor_type is AuditActorType.USER
    assert actor.user_id == user_id
    assert actor.role is None


def test_user_actor_normalizes_role_whitespace() -> None:
    actor = AuditActor.user(
        uuid4(),
        role="  ADMIN  ",
    )

    assert actor.role == "ADMIN"


def test_system_actor_has_no_user_attribution() -> None:
    actor = AuditActor.system()

    assert actor.actor_type is AuditActorType.SYSTEM
    assert actor.user_id is None
    assert actor.role is None


def test_actor_contract_is_immutable() -> None:
    actor = AuditActor.system()

    with pytest.raises(FrozenInstanceError):
        actor.role = "OWNER"  # type: ignore[misc]


def test_user_actor_requires_uuid_user_id() -> None:
    with pytest.raises(
        AuditLogInvalidActorError,
        match="must include a valid user_id",
    ):
        AuditActor.user(cast(UUID, "not-a-uuid"))


def test_direct_user_actor_requires_user_id() -> None:
    with pytest.raises(
        AuditLogInvalidActorError,
        match="must include a valid user_id",
    ):
        AuditActor(
            actor_type=AuditActorType.USER,
            user_id=None,
            role=None,
        )


def test_system_actor_rejects_user_id() -> None:
    with pytest.raises(
        AuditLogInvalidActorError,
        match="must not include a user_id",
    ):
        AuditActor(
            actor_type=AuditActorType.SYSTEM,
            user_id=uuid4(),
            role=None,
        )


def test_system_actor_rejects_role() -> None:
    with pytest.raises(
        AuditLogInvalidActorError,
        match="must not include a role",
    ):
        AuditActor(
            actor_type=AuditActorType.SYSTEM,
            user_id=None,
            role="OWNER",
        )


@pytest.mark.parametrize(
    "role",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def test_user_actor_rejects_blank_role(
    role: str,
) -> None:
    with pytest.raises(
        AuditLogInvalidActorError,
        match="must not be empty",
    ):
        AuditActor.user(
            uuid4(),
            role=role,
        )


def test_user_actor_rejects_oversized_role() -> None:
    with pytest.raises(
        AuditLogInvalidActorError,
        match="at most 50 characters",
    ):
        AuditActor.user(
            uuid4(),
            role="x" * 51,
        )


def test_actor_rejects_unsupported_actor_type() -> None:
    with pytest.raises(
        AuditLogInvalidActorError,
        match="supported AuditActorType",
    ):
        AuditActor(
            actor_type=cast(
                AuditActorType,
                "service",
            ),
            user_id=None,
            role=None,
        )
