from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from clinicops.api.errors import PROBLEM_MEDIA_TYPE
from clinicops.audit.enums import (
    AuditActorType,
    AuditSource,
)
from clinicops.audit.models import AuditLogEntry
from clinicops.authentication.models import AuthSession
from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import Argon2PasswordHasher
from clinicops.invitations.models import (
    Invitation,
    InvitationStatus,
)
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)

PASSWORD = "Correct-Horse-Battery-Staple-2026!"


@dataclass(frozen=True, slots=True)
class CommittedUser:
    """Committed global user available to HTTP request sessions."""

    id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class CommittedTenantAccess:
    """Committed tenant and one related membership."""

    tenant_id: UUID
    membership_id: UUID


class TenantApiDataFactory:
    """Create committed multi-tenant integration-test state."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._user_ids: set[UUID] = set()
        self._tenant_ids: set[UUID] = set()
        self._password_hash = Argon2PasswordHasher().hash(PASSWORD)

    def create_user(
        self,
        *,
        email_prefix: str,
    ) -> CommittedUser:
        email = f"{email_prefix}-{uuid4().hex}@example.com"
        user = User(
            email=email,
            status=UserStatus.ACTIVE,
            password_credential=PasswordCredential(
                password_hash=self._password_hash,
            ),
        )
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        self._user_ids.add(user.id)

        return CommittedUser(
            id=user.id,
            email=user.email,
        )

    def create_tenant(
        self,
        *,
        owner: CommittedUser,
        name: str,
        status: TenantStatus = TenantStatus.ACTIVE,
    ) -> CommittedTenantAccess:
        disabled_at = datetime.now(UTC) if status is TenantStatus.DISABLED else None
        tenant = Tenant(
            name=f"{name} {uuid4().hex[:8]}",
            status=status,
            disabled_at=disabled_at,
        )
        owner_membership = Membership(
            tenant=tenant,
            user_id=owner.id,
            role=TenantRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add(tenant)
        self._session.add(owner_membership)
        self._session.commit()
        self._session.refresh(tenant)
        self._session.refresh(owner_membership)
        self._tenant_ids.add(tenant.id)

        return CommittedTenantAccess(
            tenant_id=tenant.id,
            membership_id=owner_membership.id,
        )

    def add_membership(
        self,
        *,
        tenant_id: UUID,
        user: CommittedUser,
        role: TenantRole,
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> CommittedTenantAccess:
        membership = Membership(
            tenant_id=tenant_id,
            user_id=user.id,
            role=role,
            status=status,
            disabled_at=(datetime.now(UTC) if status is MembershipStatus.DISABLED else None),
        )
        self._session.add(membership)
        self._session.commit()
        self._session.refresh(membership)

        return CommittedTenantAccess(
            tenant_id=tenant_id,
            membership_id=membership.id,
        )

    def set_tenant_status(
        self,
        tenant_id: UUID,
        status: TenantStatus,
    ) -> None:
        tenant = self._session.get(Tenant, tenant_id)
        assert tenant is not None

        tenant.status = status
        tenant.disabled_at = datetime.now(UTC) if status is TenantStatus.DISABLED else None
        self._session.commit()

    def set_membership_status(
        self,
        membership_id: UUID,
        status: MembershipStatus,
    ) -> None:
        membership = self._session.get(
            Membership,
            membership_id,
        )
        assert membership is not None

        membership.status = status
        membership.disabled_at = datetime.now(UTC) if status is MembershipStatus.DISABLED else None
        self._session.commit()

    def set_membership_role(
        self,
        membership_id: UUID,
        role: TenantRole,
    ) -> None:
        membership = self._session.get(
            Membership,
            membership_id,
        )
        assert membership is not None

        membership.role = role
        self._session.commit()

    def persist_audit_entry(
        self,
        *,
        tenant_id: UUID,
        action: str = "tenant.created",
        resource_type: str = "tenant",
        resource_id: str | None = None,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            id=uuid4(),
            tenant_id=tenant_id,
            actor_type=AuditActorType.SYSTEM.value,
            actor_user_id=None,
            actor_role=None,
            source=AuditSource.SYSTEM.value,
            action=action,
            resource_type=resource_type,
            resource_id=(resource_id if resource_id is not None else str(tenant_id)),
            metadata_version=1,
            event_metadata={
                "origin": "isolation-test",
            },
            idempotency_key=(f"isolation-audit:{uuid4()}"),
            request_id=None,
            correlation_id=f"correlation-{uuid4()}",
            recorded_at=datetime.now(UTC),
        )
        self._session.add(entry)
        self._session.commit()
        self._session.refresh(entry)
        self._session.expunge(entry)

        return entry

    def count_invitations(
        self,
        tenant_id: UUID,
    ) -> int:
        self._session.expire_all()

        statement = (
            select(func.count()).select_from(Invitation).where(Invitation.tenant_id == tenant_id)
        )

        return int(self._session.scalar(statement) or 0)

    def invitation_status(
        self,
        invitation_id: UUID,
    ) -> InvitationStatus:
        self._session.expire_all()
        invitation = self._session.get(
            Invitation,
            invitation_id,
        )
        assert invitation is not None

        return invitation.status

    def cleanup(self) -> None:
        self._session.rollback()

        if self._user_ids:
            authentication_sessions = self._session.scalars(
                select(AuthSession).where(AuthSession.user_id.in_(self._user_ids))
            ).all()

            for authentication_session in authentication_sessions:
                self._session.delete(authentication_session)

        if self._tenant_ids:
            self._session.execute(
                delete(AuditLogEntry).where(AuditLogEntry.tenant_id.in_(self._tenant_ids))
            )

            invitations = self._session.scalars(
                select(Invitation).where(Invitation.tenant_id.in_(self._tenant_ids))
            ).all()

            for invitation in invitations:
                self._session.delete(invitation)

            memberships = self._session.scalars(
                select(Membership).where(Membership.tenant_id.in_(self._tenant_ids))
            ).all()

            for membership in memberships:
                self._session.delete(membership)

            tenants = self._session.scalars(
                select(Tenant).where(Tenant.id.in_(self._tenant_ids))
            ).all()

            for tenant in tenants:
                self._session.delete(tenant)

        if self._user_ids:
            users = self._session.scalars(select(User).where(User.id.in_(self._user_ids))).all()

            for user in users:
                self._session.delete(user)

        self._session.commit()


@pytest.fixture
def tenant_api_data_factory() -> Iterator[TenantApiDataFactory]:
    session = Session(get_engine())
    factory = TenantApiDataFactory(session)

    try:
        yield factory
    finally:
        factory.cleanup()
        session.close()


def login_headers(
    client: TestClient,
    user: CommittedUser,
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": PASSWORD,
        },
    )

    assert response.status_code == 200

    return {"Authorization": (f"Bearer {response.json()['access_token']}")}


def assert_problem(
    response: Response,
    *,
    status_code: int,
    code: str,
) -> None:
    body = response.json()

    assert response.status_code == status_code
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["status"] == status_code
    assert body["code"] == code
    assert body["type"] == f"urn:clinicops:problem:{code}"


def issue_invitation(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
    email_prefix: str,
) -> Response:
    return client.post(
        f"/api/v1/tenants/{tenant_id}/invitations",
        headers=headers,
        json={
            "invited_email": (f"{email_prefix}-{uuid4().hex}@example.com"),
            "role": "staff",
        },
    )


def test_available_tenant_list_excludes_inactive_access(
    client: TestClient,
    tenant_api_data_factory: TenantApiDataFactory,
) -> None:
    user = tenant_api_data_factory.create_user(email_prefix="tenant-list-user")
    other_owner = tenant_api_data_factory.create_user(email_prefix="tenant-list-owner")
    visible_tenant = tenant_api_data_factory.create_tenant(
        owner=user,
        name="Visible Clinic",
    )
    disabled_membership_tenant = tenant_api_data_factory.create_tenant(
        owner=other_owner,
        name="Disabled Membership Clinic",
    )
    tenant_api_data_factory.add_membership(
        tenant_id=disabled_membership_tenant.tenant_id,
        user=user,
        role=TenantRole.STAFF,
        status=MembershipStatus.DISABLED,
    )
    tenant_api_data_factory.create_tenant(
        owner=user,
        name="Disabled Tenant Clinic",
        status=TenantStatus.DISABLED,
    )
    headers = login_headers(client, user)

    response = client.get(
        "/api/v1/tenants",
        headers=headers,
    )

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {
        str(visible_tenant.tenant_id),
    }


def test_user_cannot_access_another_tenant_by_path_id(
    client: TestClient,
    tenant_api_data_factory: TenantApiDataFactory,
) -> None:
    user = tenant_api_data_factory.create_user(email_prefix="isolated-user")
    other_owner = tenant_api_data_factory.create_user(email_prefix="isolated-owner")
    tenant_api_data_factory.create_tenant(
        owner=user,
        name="User Clinic",
    )
    foreign_tenant = tenant_api_data_factory.create_tenant(
        owner=other_owner,
        name="Foreign Clinic",
    )
    headers = login_headers(client, user)

    detail_response = client.get(
        (f"/api/v1/tenants/{foreign_tenant.tenant_id}"),
        headers=headers,
    )
    memberships_response = client.get(
        (f"/api/v1/tenants/{foreign_tenant.tenant_id}/memberships"),
        headers=headers,
    )

    assert detail_response.status_code == 404
    assert memberships_response.status_code == 404
    assert detail_response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert memberships_response.headers["content-type"] == PROBLEM_MEDIA_TYPE


def test_disabled_tenant_and_membership_affect_the_next_request(
    client: TestClient,
    tenant_api_data_factory: TenantApiDataFactory,
) -> None:
    user = tenant_api_data_factory.create_user(email_prefix="state-user")
    owner = tenant_api_data_factory.create_user(email_prefix="state-owner")
    tenant = tenant_api_data_factory.create_tenant(
        owner=owner,
        name="State Clinic",
    )
    user_access = tenant_api_data_factory.add_membership(
        tenant_id=tenant.tenant_id,
        user=user,
        role=TenantRole.ADMIN,
    )
    headers = login_headers(client, user)
    endpoint = f"/api/v1/tenants/{tenant.tenant_id}"

    assert (
        client.get(
            endpoint,
            headers=headers,
        ).status_code
        == 200
    )

    tenant_api_data_factory.set_tenant_status(
        tenant.tenant_id,
        TenantStatus.DISABLED,
    )

    disabled_tenant_response = client.get(
        endpoint,
        headers=headers,
    )
    assert_problem(
        disabled_tenant_response,
        status_code=403,
        code="tenant_disabled",
    )

    tenant_api_data_factory.set_tenant_status(
        tenant.tenant_id,
        TenantStatus.ACTIVE,
    )
    tenant_api_data_factory.set_membership_status(
        user_access.membership_id,
        MembershipStatus.DISABLED,
    )

    disabled_membership_response = client.get(
        endpoint,
        headers=headers,
    )
    assert_problem(
        disabled_membership_response,
        status_code=403,
        code="tenant_membership_disabled",
    )


def test_role_demotion_removes_invitation_permission_on_next_request(
    client: TestClient,
    tenant_api_data_factory: TenantApiDataFactory,
) -> None:
    user = tenant_api_data_factory.create_user(email_prefix="role-user")
    owner = tenant_api_data_factory.create_user(email_prefix="role-owner")
    tenant = tenant_api_data_factory.create_tenant(
        owner=owner,
        name="Role Clinic",
    )
    user_access = tenant_api_data_factory.add_membership(
        tenant_id=tenant.tenant_id,
        user=user,
        role=TenantRole.ADMIN,
    )
    headers = login_headers(client, user)

    allowed_response = issue_invitation(
        client,
        tenant_id=tenant.tenant_id,
        headers=headers,
        email_prefix="allowed-invite",
    )

    assert allowed_response.status_code == 201
    assert tenant_api_data_factory.count_invitations(tenant.tenant_id) == 1

    tenant_api_data_factory.set_membership_role(
        user_access.membership_id,
        TenantRole.STAFF,
    )

    denied_response = issue_invitation(
        client,
        tenant_id=tenant.tenant_id,
        headers=headers,
        email_prefix="denied-invite",
    )

    assert_problem(
        denied_response,
        status_code=403,
        code="tenant_permission_denied",
    )
    assert tenant_api_data_factory.count_invitations(tenant.tenant_id) == 1


def test_invitation_queries_and_revocation_preserve_tenant_ownership(
    client: TestClient,
    tenant_api_data_factory: TenantApiDataFactory,
) -> None:
    owner = tenant_api_data_factory.create_user(email_prefix="invitation-owner")
    first_tenant = tenant_api_data_factory.create_tenant(
        owner=owner,
        name="First Invitation Clinic",
    )
    second_tenant = tenant_api_data_factory.create_tenant(
        owner=owner,
        name="Second Invitation Clinic",
    )
    headers = login_headers(client, owner)

    first_issue = issue_invitation(
        client,
        tenant_id=first_tenant.tenant_id,
        headers=headers,
        email_prefix="first-tenant-invite",
    )
    second_issue = issue_invitation(
        client,
        tenant_id=second_tenant.tenant_id,
        headers=headers,
        email_prefix="second-tenant-invite",
    )

    assert first_issue.status_code == 201
    assert second_issue.status_code == 201

    first_invitation_id = UUID(first_issue.json()["id"])
    second_invitation_id = UUID(second_issue.json()["id"])

    first_list = client.get(
        (f"/api/v1/tenants/{first_tenant.tenant_id}/invitations"),
        headers=headers,
    )

    assert first_list.status_code == 200
    assert {item["id"] for item in first_list.json()["items"]} == {
        str(first_invitation_id),
    }

    cross_tenant_revoke = client.post(
        (f"/api/v1/tenants/{first_tenant.tenant_id}/invitations/{second_invitation_id}/revoke"),
        headers=headers,
    )

    assert_problem(
        cross_tenant_revoke,
        status_code=404,
        code="invitation_not_found",
    )
    assert (
        tenant_api_data_factory.invitation_status(second_invitation_id) is InvitationStatus.PENDING
    )


def test_invitation_body_cannot_override_path_tenant(
    client: TestClient,
    tenant_api_data_factory: TenantApiDataFactory,
) -> None:
    owner = tenant_api_data_factory.create_user(email_prefix="payload-owner")
    path_tenant = tenant_api_data_factory.create_tenant(
        owner=owner,
        name="Path Tenant Clinic",
    )
    foreign_tenant = tenant_api_data_factory.create_tenant(
        owner=owner,
        name="Payload Tenant Clinic",
    )
    headers = login_headers(client, owner)

    response = client.post(
        (f"/api/v1/tenants/{path_tenant.tenant_id}/invitations"),
        headers=headers,
        json={
            "tenant_id": str(foreign_tenant.tenant_id),
            "invited_email": (f"payload-{uuid4().hex}@example.com"),
            "role": "staff",
        },
    )

    assert_problem(
        response,
        status_code=422,
        code="request_validation_error",
    )
    assert tenant_api_data_factory.count_invitations(path_tenant.tenant_id) == 0
    assert tenant_api_data_factory.count_invitations(foreign_tenant.tenant_id) == 0


def test_audit_log_authorization_and_tenant_isolation(
    client: TestClient,
    tenant_api_data_factory: TenantApiDataFactory,
) -> None:
    owner = tenant_api_data_factory.create_user(email_prefix="audit-auth-owner")
    admin = tenant_api_data_factory.create_user(email_prefix="audit-auth-admin")
    staff = tenant_api_data_factory.create_user(email_prefix="audit-auth-staff")
    foreign_owner = tenant_api_data_factory.create_user(email_prefix="audit-auth-foreign")

    tenant = tenant_api_data_factory.create_tenant(
        owner=owner,
        name="Audit Auth Clinic",
    )
    foreign_tenant = tenant_api_data_factory.create_tenant(
        owner=foreign_owner,
        name="Foreign Audit Clinic",
    )
    tenant_api_data_factory.add_membership(
        tenant_id=tenant.tenant_id,
        user=admin,
        role=TenantRole.ADMIN,
    )
    tenant_api_data_factory.add_membership(
        tenant_id=tenant.tenant_id,
        user=staff,
        role=TenantRole.STAFF,
    )

    owned_entry = tenant_api_data_factory.persist_audit_entry(
        tenant_id=tenant.tenant_id,
        action="membership.role_changed",
        resource_type="membership",
        resource_id=str(uuid4()),
    )
    foreign_entry = tenant_api_data_factory.persist_audit_entry(
        tenant_id=foreign_tenant.tenant_id,
        action="invitation.created",
        resource_type="invitation",
        resource_id=str(uuid4()),
    )

    owner_headers = login_headers(client, owner)
    admin_headers = login_headers(client, admin)
    staff_headers = login_headers(client, staff)
    foreign_headers = login_headers(client, foreign_owner)
    audit_path = f"/api/v1/tenants/{tenant.tenant_id}/audit-logs"

    owner_response = client.get(
        audit_path,
        headers=owner_headers,
    )
    admin_response = client.get(
        audit_path,
        headers=admin_headers,
    )
    staff_response = client.get(
        audit_path,
        headers=staff_headers,
    )
    foreign_response = client.get(
        audit_path,
        headers=foreign_headers,
    )

    assert owner_response.status_code == 200
    assert admin_response.status_code == 200

    owner_body = owner_response.json()
    admin_body = admin_response.json()

    assert {item["id"] for item in owner_body["items"]} == {str(owned_entry.id)}
    assert {item["id"] for item in admin_body["items"]} == {str(owned_entry.id)}
    assert str(foreign_entry.id) not in owner_response.text
    assert str(foreign_entry.id) not in admin_response.text
    assert "idempotency_key" not in owner_body
    assert "idempotency_key" not in owner_body["items"][0]

    assert_problem(
        staff_response,
        status_code=403,
        code="tenant_permission_denied",
    )
    assert staff_response.status_code == 403
    assert foreign_response.status_code == 404
    assert foreign_response.headers["content-type"] == PROBLEM_MEDIA_TYPE
