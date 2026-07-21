from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.tenancy.exceptions import (
    MembershipActorNotAuthorizedError,
    MembershipAlreadyActiveError,
    MembershipAlreadyDisabledError,
    MembershipOwnerProtectedError,
    MembershipRoleNotAllowedError,
    MembershipSelfManagementNotAllowedError,
    TenancyError,
)
from clinicops.tenancy.models import TenantRole
from clinicops.tenancy.services.membership_administration import (
    ChangedMembershipRole,
    ChangeMembershipRoleCommand,
    DisabledMembership,
    DisableMembershipCommand,
    EnabledMembership,
    EnableMembershipCommand,
    RemovedMembership,
    RemoveMembershipCommand,
)

FIXED_NOW = datetime(2026, 8, 26, 15, 0, tzinfo=UTC)


def test_membership_administration_commands_carry_trusted_identity() -> None:
    tenant_id = uuid4()
    actor_user_id = uuid4()
    membership_id = uuid4()

    assert ChangeMembershipRoleCommand(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        membership_id=membership_id,
        role=TenantRole.ADMIN,
    ) == ChangeMembershipRoleCommand(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        membership_id=membership_id,
        role=TenantRole.ADMIN,
    )
    assert (
        DisableMembershipCommand(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
        ).actor_user_id
        == actor_user_id
    )
    assert (
        EnableMembershipCommand(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
        ).tenant_id
        == tenant_id
    )
    assert (
        RemoveMembershipCommand(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
        ).membership_id
        == membership_id
    )


def test_membership_role_result_preserves_previous_and_current_role() -> None:
    result = ChangedMembershipRole(
        membership_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        previous_role=TenantRole.STAFF,
        role=TenantRole.ADMIN,
        updated_at=FIXED_NOW,
    )

    assert result.previous_role is TenantRole.STAFF
    assert result.role is TenantRole.ADMIN
    assert result.updated_at == FIXED_NOW


def test_membership_status_results_expose_lifecycle_state() -> None:
    membership_id = uuid4()
    tenant_id = uuid4()
    user_id = uuid4()

    disabled = DisabledMembership(
        membership_id=membership_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=TenantRole.STAFF,
        disabled_at=FIXED_NOW,
    )
    enabled = EnabledMembership(
        membership_id=membership_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=TenantRole.STAFF,
        updated_at=FIXED_NOW,
    )

    assert disabled.disabled_at == FIXED_NOW
    assert enabled.updated_at == FIXED_NOW
    assert disabled.user_id == enabled.user_id


def test_removed_membership_result_contains_no_user_deletion_signal() -> None:
    result = RemovedMembership(
        membership_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
    )
    result_fields = {field.name for field in fields(RemovedMembership)}

    assert result_fields == {
        "membership_id",
        "tenant_id",
        "user_id",
    }
    assert result.user_id is not None


def test_membership_administration_contracts_are_frozen() -> None:
    command = DisableMembershipCommand(
        tenant_id=uuid4(),
        actor_user_id=uuid4(),
        membership_id=uuid4(),
    )

    with pytest.raises(FrozenInstanceError):
        command.membership_id = uuid4()  # type: ignore[misc]


@pytest.mark.parametrize(
    (
        "exception",
        "expected_code",
        "expected_message",
    ),
    [
        (
            MembershipActorNotAuthorizedError(),
            "membership_actor_not_authorized",
            "The actor cannot manage tenant memberships.",
        ),
        (
            MembershipRoleNotAllowedError(),
            "membership_role_not_allowed",
            "The membership role is not allowed.",
        ),
        (
            MembershipOwnerProtectedError(),
            "membership_owner_protected",
            "The tenant owner membership is protected.",
        ),
        (
            MembershipSelfManagementNotAllowedError(),
            "membership_self_management_not_allowed",
            "The actor cannot manage their own membership.",
        ),
        (
            MembershipAlreadyDisabledError(),
            "membership_already_disabled",
            "The membership is already disabled.",
        ),
        (
            MembershipAlreadyActiveError(),
            "membership_already_active",
            "The membership is already active.",
        ),
    ],
)
def test_membership_administration_exceptions_have_stable_contracts(
    exception: TenancyError,
    expected_code: str,
    expected_message: str,
) -> None:
    assert exception.code == expected_code
    assert exception.public_message == expected_message
