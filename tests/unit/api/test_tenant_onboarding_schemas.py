from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinicops.api.v1.invitations.schemas import (
    AcceptedInvitationResponse,
    AcceptInvitationRequest,
)
from clinicops.api.v1.tenants.schemas import (
    CreatedTenantResponse,
    CreateTenantRequest,
)
from clinicops.invitations.services.accept_invitation import (
    AcceptedInvitation,
)
from clinicops.tenancy.models import TenantRole, TenantStatus
from clinicops.tenancy.services.create_tenant import (
    CreatedTenant,
)

FIXED_NOW = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)


def test_create_tenant_request_normalizes_name() -> None:
    request = CreateTenantRequest(name="  Northstar Health Clinic  ")

    assert request.name == "Northstar Health Clinic"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": " "},
        {"name": "a" * 121},
        {
            "name": "Northstar Health Clinic",
            "owner_user_id": str(uuid4()),
        },
        {
            "name": "Northstar Health Clinic",
            "owner_role": "owner",
        },
        {
            "name": "Northstar Health Clinic",
            "status": "active",
        },
    ],
)
def test_create_tenant_request_rejects_invalid_contract(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        CreateTenantRequest.model_validate(payload)


def test_created_tenant_response_maps_application_result() -> None:
    tenant_id = uuid4()
    owner_user_id = uuid4()
    result = CreatedTenant(
        id=tenant_id,
        name="Northstar Health Clinic",
        status=TenantStatus.ACTIVE,
        owner_user_id=owner_user_id,
        created_at=FIXED_NOW,
    )

    response = CreatedTenantResponse.model_validate(result)

    assert response == CreatedTenantResponse(
        id=tenant_id,
        name="Northstar Health Clinic",
        status=TenantStatus.ACTIVE,
        owner_user_id=owner_user_id,
        created_at=FIXED_NOW,
    )


def test_accept_invitation_request_preserves_and_masks_secrets() -> None:
    token = " token-with-deliberate-whitespace "
    password = " Password-With-Spaces-2026 "
    request = AcceptInvitationRequest(
        token=token,
        password=password,
    )

    assert request.token == token
    assert request.password == password
    assert token not in repr(request)
    assert password not in repr(request)
    assert request.model_dump() == {
        "token": token,
        "password": password,
    }


def test_accept_invitation_request_allows_missing_password() -> None:
    request = AcceptInvitationRequest(
        token="existing-user-token",
    )

    assert request.token == "existing-user-token"
    assert request.password is None


@pytest.mark.parametrize(
    "payload",
    [
        {"token": ""},
        {
            "token": "valid-token",
            "password": "too-short",
        },
        {
            "token": "valid-token",
            "tenant_id": str(uuid4()),
        },
        {
            "token": "valid-token",
            "role": "admin",
        },
        {
            "token": "valid-token",
            "invited_email": "override@example.com",
        },
        {
            "token": "valid-token",
            "accepted_by_user_id": str(uuid4()),
        },
    ],
)
def test_accept_invitation_request_rejects_invalid_contract(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        AcceptInvitationRequest.model_validate(payload)


def test_accepted_invitation_response_maps_application_result() -> None:
    result = AcceptedInvitation(
        invitation_id=uuid4(),
        tenant_id=uuid4(),
        membership_id=uuid4(),
        user_id=uuid4(),
        role=TenantRole.ADMIN,
        user_was_created=True,
        accepted_at=FIXED_NOW,
    )

    response = AcceptedInvitationResponse.model_validate(result)

    assert response.invitation_id == result.invitation_id
    assert response.tenant_id == result.tenant_id
    assert response.membership_id == result.membership_id
    assert response.user_id == result.user_id
    assert response.role is TenantRole.ADMIN
    assert response.user_was_created is True
    assert response.accepted_at == FIXED_NOW


def test_acceptance_response_exposes_no_invitation_credentials() -> None:
    assert "token" not in AcceptedInvitationResponse.model_fields
    assert "password" not in AcceptedInvitationResponse.model_fields
    assert "invited_email" not in (AcceptedInvitationResponse.model_fields)
