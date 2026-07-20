from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinicops.api.v1.tenants.schemas import (
    CurrentMembershipResponse,
    InvitationListResponse,
    InvitationResponse,
    IssuedInvitationResponse,
    IssueInvitationRequest,
    MembershipListResponse,
    MembershipResponse,
    RevokedInvitationResponse,
    TenantDetailResponse,
    TenantListResponse,
    TenantSummaryResponse,
)
from clinicops.invitations.models import InvitationStatus
from clinicops.invitations.services.issue_invitation import (
    IssuedInvitation,
)
from clinicops.invitations.services.revoke_invitation import (
    RevokedInvitation,
)
from clinicops.tenancy.models import (
    MembershipStatus,
    TenantRole,
    TenantStatus,
)

FIXED_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def test_issue_invitation_request_normalizes_public_input() -> None:
    request = IssueInvitationRequest(
        invited_email="  New.Member@Example.com  ",
        role=TenantRole.ADMIN,
    )

    assert request.invited_email == "New.Member@Example.com"
    assert request.role is TenantRole.ADMIN


@pytest.mark.parametrize(
    "payload",
    [
        {
            "invited_email": "member@example.com",
            "role": "owner",
        },
        {
            "invited_email": "",
            "role": "staff",
        },
        {
            "invited_email": "member@example.com",
            "role": "staff",
            "tenant_id": str(uuid4()),
        },
    ],
)
def test_issue_invitation_request_rejects_invalid_contract(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        IssueInvitationRequest.model_validate(payload)


def test_issued_invitation_response_maps_application_result_and_masks_repr() -> None:
    invitation_id = uuid4()
    tenant_id = uuid4()
    plaintext_token = "invitation-secret"
    result = IssuedInvitation(
        id=invitation_id,
        tenant_id=tenant_id,
        invited_email="member@example.com",
        role=TenantRole.STAFF,
        expires_at=FIXED_NOW + timedelta(days=7),
        token=plaintext_token,
    )

    response = IssuedInvitationResponse.model_validate(result)

    assert response.id == invitation_id
    assert response.tenant_id == tenant_id
    assert response.role is TenantRole.STAFF
    assert response.token == plaintext_token
    assert plaintext_token not in repr(response)
    assert response.model_dump()["token"] == plaintext_token


def test_revoked_invitation_response_maps_application_result() -> None:
    invitation_id = uuid4()
    tenant_id = uuid4()
    result = RevokedInvitation(
        invitation_id=invitation_id,
        tenant_id=tenant_id,
        revoked_at=FIXED_NOW,
    )

    response = RevokedInvitationResponse.model_validate(result)

    assert response == RevokedInvitationResponse(
        invitation_id=invitation_id,
        tenant_id=tenant_id,
        revoked_at=FIXED_NOW,
    )


def test_tenant_contracts_keep_current_membership_explicit() -> None:
    tenant_id = uuid4()
    membership_id = uuid4()
    current_membership = CurrentMembershipResponse(
        id=membership_id,
        role=TenantRole.ADMIN,
        status=MembershipStatus.ACTIVE,
    )
    summary = TenantSummaryResponse(
        id=tenant_id,
        name="  North Clinic  ",
        status=TenantStatus.ACTIVE,
        current_membership=current_membership,
    )
    detail = TenantDetailResponse(
        id=tenant_id,
        name="North Clinic",
        status=TenantStatus.ACTIVE,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        disabled_at=None,
        current_membership=current_membership,
    )
    tenant_list = TenantListResponse(items=[summary])

    assert summary.name == "North Clinic"
    assert summary.current_membership.id == membership_id
    assert detail.current_membership.role is TenantRole.ADMIN
    assert tenant_list.items == [summary]


def test_membership_contract_exposes_lifecycle_without_user_credentials() -> None:
    membership = MembershipResponse(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        role=TenantRole.STAFF,
        status=MembershipStatus.DISABLED,
        created_at=FIXED_NOW - timedelta(days=30),
        updated_at=FIXED_NOW,
        disabled_at=FIXED_NOW,
    )
    response = MembershipListResponse(items=[membership])
    serialized = response.model_dump(mode="json")

    assert serialized["items"][0]["role"] == "staff"
    assert serialized["items"][0]["status"] == "disabled"
    assert set(serialized["items"][0]) == {
        "id",
        "tenant_id",
        "user_id",
        "role",
        "status",
        "created_at",
        "updated_at",
        "disabled_at",
    }


def test_invitation_contract_does_not_expose_digest_or_internal_actor_fields() -> None:
    source = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        invited_email="member@example.com",
        role=TenantRole.ADMIN,
        status=InvitationStatus.PENDING,
        token_digest="sensitive-digest",
        created_by_membership_id=uuid4(),
        accepted_by_user_id=None,
        expires_at=FIXED_NOW + timedelta(days=7),
        accepted_at=None,
        revoked_at=None,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )

    invitation = InvitationResponse.model_validate(source)
    response = InvitationListResponse(items=[invitation])
    serialized = response.model_dump(mode="json")["items"][0]

    assert serialized["status"] == "pending"
    assert "token_digest" not in serialized
    assert "created_by_membership_id" not in serialized
    assert "accepted_by_user_id" not in serialized
    assert "token_digest" not in InvitationResponse.model_fields
    assert "token" not in InvitationResponse.model_fields


def test_response_contracts_reject_unexpected_mapping_fields() -> None:
    with pytest.raises(ValidationError):
        MembershipResponse.model_validate(
            {
                "id": uuid4(),
                "tenant_id": uuid4(),
                "user_id": uuid4(),
                "role": "staff",
                "status": "active",
                "created_at": FIXED_NOW,
                "updated_at": FIXED_NOW,
                "disabled_at": None,
                "unexpected": "value",
            }
        )


def test_list_contracts_use_wrapped_items_for_future_metadata() -> None:
    assert set(TenantListResponse.model_fields) == {"items"}
    assert set(MembershipListResponse.model_fields) == {"items"}
    assert set(InvitationListResponse.model_fields) == {"items"}
