from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from clinicops.api.v1.tenants.schemas import (
    ChangedMembershipRoleResponse,
    ChangeMembershipRoleRequest,
    DisabledMembershipResponse,
    EnabledMembershipResponse,
)
from clinicops.tenancy.models import TenantRole
from clinicops.tenancy.services.membership_administration import (
    ChangedMembershipRole,
    DisabledMembership,
    EnabledMembership,
)

FIXED_NOW = datetime(2026, 8, 29, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "role",
    [
        TenantRole.ADMIN,
        TenantRole.STAFF,
        "admin",
        "staff",
    ],
)
def test_change_membership_role_request_accepts_manageable_roles(
    role: TenantRole | str,
) -> None:
    request = ChangeMembershipRoleRequest.model_validate({"role": role})

    assert request.role in {
        TenantRole.ADMIN,
        TenantRole.STAFF,
    }


def test_change_membership_role_request_rejects_owner() -> None:
    with pytest.raises(ValidationError):
        ChangeMembershipRoleRequest.model_validate({"role": TenantRole.OWNER})


@pytest.mark.parametrize(
    "field_name",
    [
        "tenant_id",
        "actor_user_id",
        "membership_id",
        "user_id",
        "status",
    ],
)
def test_change_membership_role_request_rejects_trusted_fields(
    field_name: str,
) -> None:
    payload: dict[str, object] = {
        "role": "admin",
        field_name: str(uuid4()),
    }

    with pytest.raises(ValidationError):
        ChangeMembershipRoleRequest.model_validate(payload)


def test_changed_membership_role_response_matches_application_result() -> None:
    result = ChangedMembershipRole(
        membership_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        previous_role=TenantRole.STAFF,
        role=TenantRole.ADMIN,
        updated_at=FIXED_NOW,
    )

    response = ChangedMembershipRoleResponse.model_validate(result)

    assert response.model_dump() == {
        "membership_id": result.membership_id,
        "tenant_id": result.tenant_id,
        "user_id": result.user_id,
        "previous_role": TenantRole.STAFF,
        "role": TenantRole.ADMIN,
        "updated_at": FIXED_NOW,
    }


def test_disabled_membership_response_matches_application_result() -> None:
    result = DisabledMembership(
        membership_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        role=TenantRole.ADMIN,
        disabled_at=FIXED_NOW,
    )

    response = DisabledMembershipResponse.model_validate(result)

    assert response.model_dump() == {
        "membership_id": result.membership_id,
        "tenant_id": result.tenant_id,
        "user_id": result.user_id,
        "role": TenantRole.ADMIN,
        "disabled_at": FIXED_NOW,
    }


def test_enabled_membership_response_matches_application_result() -> None:
    result = EnabledMembership(
        membership_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        role=TenantRole.STAFF,
        updated_at=FIXED_NOW,
    )

    response = EnabledMembershipResponse.model_validate(result)

    assert response.model_dump() == {
        "membership_id": result.membership_id,
        "tenant_id": result.tenant_id,
        "user_id": result.user_id,
        "role": TenantRole.STAFF,
        "updated_at": FIXED_NOW,
    }


@pytest.mark.parametrize(
    (
        "response_type",
        "payload",
    ),
    [
        (
            ChangedMembershipRoleResponse,
            {
                "membership_id": uuid4(),
                "tenant_id": uuid4(),
                "user_id": uuid4(),
                "previous_role": TenantRole.STAFF,
                "role": TenantRole.ADMIN,
                "updated_at": FIXED_NOW,
                "actor_user_id": uuid4(),
            },
        ),
        (
            DisabledMembershipResponse,
            {
                "membership_id": uuid4(),
                "tenant_id": uuid4(),
                "user_id": uuid4(),
                "role": TenantRole.STAFF,
                "disabled_at": FIXED_NOW,
                "password": "not-public",
            },
        ),
        (
            EnabledMembershipResponse,
            {
                "membership_id": uuid4(),
                "tenant_id": uuid4(),
                "user_id": uuid4(),
                "role": TenantRole.STAFF,
                "updated_at": FIXED_NOW,
                "access_token": "not-public",
            },
        ),
    ],
)
def test_membership_administration_responses_reject_extra_fields(
    response_type: type[BaseModel],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        response_type.model_validate(payload)


def test_membership_administration_responses_are_frozen() -> None:
    response = DisabledMembershipResponse(
        membership_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        role=TenantRole.STAFF,
        disabled_at=FIXED_NOW,
    )

    with pytest.raises(ValidationError):
        response.role = TenantRole.ADMIN
