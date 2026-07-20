from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.tenancy.exceptions import TenantNotFoundError
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.query_repository import TenantQueryRepository
from clinicops.tenancy.services.queries import (
    GetTenantDetailsCommand,
    GetTenantDetailsService,
    ListAvailableTenantsCommand,
    ListAvailableTenantsService,
    ListTenantMembershipsCommand,
    ListTenantMembershipsService,
)

FIXED_NOW = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)


class RecordingSession:
    """Track accidental transaction completion in query services."""

    def __init__(self) -> None:
        self.commit_count = 0
        self.flush_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def flush(self) -> None:
        self.flush_count += 1


class FakeTenantQueryRepository:
    """Return deterministic tenant query records."""

    def __init__(
        self,
        *,
        available_memberships: list[Membership] | None = None,
        tenant: Tenant | None = None,
        memberships: list[Membership] | None = None,
    ) -> None:
        self.available_memberships = available_memberships or []
        self.tenant = tenant
        self.memberships = memberships or []
        self.available_user_id: UUID | None = None
        self.requested_tenant_id: UUID | None = None
        self.membership_tenant_id: UUID | None = None

    def list_available_memberships(
        self,
        session: Session,
        user_id: UUID,
    ) -> list[Membership]:
        self.available_user_id = user_id
        return self.available_memberships

    def get_tenant(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> Tenant | None:
        self.requested_tenant_id = tenant_id
        return self.tenant

    def list_memberships(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> list[Membership]:
        self.membership_tenant_id = tenant_id
        return self.memberships


def build_tenant(
    *,
    name: str = "North Clinic",
    status: TenantStatus = TenantStatus.ACTIVE,
) -> Tenant:
    return Tenant(
        id=uuid4(),
        name=name,
        status=status,
        created_at=FIXED_NOW - timedelta(days=90),
        updated_at=FIXED_NOW,
        disabled_at=(FIXED_NOW if status is TenantStatus.DISABLED else None),
    )


def build_membership(
    tenant: Tenant,
    *,
    user_id: UUID | None = None,
    role: TenantRole = TenantRole.STAFF,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> Membership:
    return Membership(
        id=uuid4(),
        tenant_id=tenant.id,
        tenant=tenant,
        user_id=user_id or uuid4(),
        role=role,
        status=status,
        created_at=FIXED_NOW - timedelta(days=30),
        updated_at=FIXED_NOW,
        disabled_at=(FIXED_NOW if status is MembershipStatus.DISABLED else None),
    )


def test_list_available_tenants_maps_current_membership_without_writes() -> None:
    user_id = uuid4()
    first_tenant = build_tenant(name="Alpha Clinic")
    second_tenant = build_tenant(name="Beta Clinic")
    first_membership = build_membership(
        first_tenant,
        user_id=user_id,
        role=TenantRole.OWNER,
    )
    second_membership = build_membership(
        second_tenant,
        user_id=user_id,
        role=TenantRole.ADMIN,
    )
    repository = FakeTenantQueryRepository(
        available_memberships=[
            first_membership,
            second_membership,
        ]
    )
    session = RecordingSession()
    service = ListAvailableTenantsService(cast(TenantQueryRepository, repository))

    results = service.execute(
        cast(Session, session),
        ListAvailableTenantsCommand(user_id=user_id),
    )

    assert repository.available_user_id == user_id
    assert [result.tenant_id for result in results] == [
        first_tenant.id,
        second_tenant.id,
    ]
    assert results[0].membership_id == first_membership.id
    assert results[0].membership_role is TenantRole.OWNER
    assert results[1].membership_role is TenantRole.ADMIN
    assert session.commit_count == 0
    assert session.flush_count == 0


def test_get_tenant_details_maps_read_model_without_writes() -> None:
    tenant = build_tenant()
    repository = FakeTenantQueryRepository(tenant=tenant)
    session = RecordingSession()
    service = GetTenantDetailsService(cast(TenantQueryRepository, repository))

    result = service.execute(
        cast(Session, session),
        GetTenantDetailsCommand(tenant_id=tenant.id),
    )

    assert repository.requested_tenant_id == tenant.id
    assert result.id == tenant.id
    assert result.name == tenant.name
    assert result.status is TenantStatus.ACTIVE
    assert result.created_at == tenant.created_at
    assert result.updated_at == tenant.updated_at
    assert result.disabled_at is None
    assert session.commit_count == 0
    assert session.flush_count == 0


def test_get_tenant_details_preserves_not_found_failure() -> None:
    repository = FakeTenantQueryRepository(tenant=None)
    service = GetTenantDetailsService(cast(TenantQueryRepository, repository))

    with pytest.raises(TenantNotFoundError):
        service.execute(
            cast(Session, RecordingSession()),
            GetTenantDetailsCommand(tenant_id=uuid4()),
        )


def test_list_tenant_memberships_includes_active_and_disabled_records() -> None:
    tenant = build_tenant()
    active_membership = build_membership(
        tenant,
        role=TenantRole.ADMIN,
    )
    disabled_membership = build_membership(
        tenant,
        role=TenantRole.STAFF,
        status=MembershipStatus.DISABLED,
    )
    repository = FakeTenantQueryRepository(
        memberships=[
            active_membership,
            disabled_membership,
        ]
    )
    session = RecordingSession()
    service = ListTenantMembershipsService(cast(TenantQueryRepository, repository))

    results = service.execute(
        cast(Session, session),
        ListTenantMembershipsCommand(tenant_id=tenant.id),
    )

    assert repository.membership_tenant_id == tenant.id
    assert [result.id for result in results] == [
        active_membership.id,
        disabled_membership.id,
    ]
    assert results[0].status is MembershipStatus.ACTIVE
    assert results[1].status is MembershipStatus.DISABLED
    assert results[1].disabled_at == FIXED_NOW
    assert session.commit_count == 0
    assert session.flush_count == 0
