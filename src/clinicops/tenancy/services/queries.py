from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.tenancy.exceptions import TenantNotFoundError
from clinicops.tenancy.models import (
    MembershipStatus,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.query_repository import TenantQueryRepository


@dataclass(frozen=True, slots=True)
class ListAvailableTenantsCommand:
    """Global user identity used to discover available tenants."""

    user_id: UUID


@dataclass(frozen=True, slots=True)
class AvailableTenant:
    """Tenant and current membership visible to one user."""

    tenant_id: UUID
    tenant_name: str
    tenant_status: TenantStatus
    membership_id: UUID
    membership_role: TenantRole
    membership_status: MembershipStatus


@dataclass(frozen=True, slots=True)
class GetTenantDetailsCommand:
    """Trusted selected tenant used for one detail query."""

    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class TenantDetails:
    """Read-only tenant details."""

    id: UUID
    name: str
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None


@dataclass(frozen=True, slots=True)
class ListTenantMembershipsCommand:
    """Trusted selected tenant used for membership discovery."""

    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class TenantMembership:
    """Read-only membership details."""

    id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None


class ListAvailableTenantsService:
    """List active tenant access available to one global user."""

    def __init__(
        self,
        query_repository: TenantQueryRepository | None = None,
    ) -> None:
        self._query_repository = (
            query_repository if query_repository is not None else TenantQueryRepository()
        )

    def execute(
        self,
        session: Session,
        command: ListAvailableTenantsCommand,
    ) -> tuple[AvailableTenant, ...]:
        """Return tenants backed by current active memberships."""

        memberships = self._query_repository.list_available_memberships(
            session,
            command.user_id,
        )

        return tuple(
            AvailableTenant(
                tenant_id=membership.tenant.id,
                tenant_name=membership.tenant.name,
                tenant_status=membership.tenant.status,
                membership_id=membership.id,
                membership_role=membership.role,
                membership_status=membership.status,
            )
            for membership in memberships
        )


class GetTenantDetailsService:
    """Read one tenant without acquiring a database row lock."""

    def __init__(
        self,
        query_repository: TenantQueryRepository | None = None,
    ) -> None:
        self._query_repository = (
            query_repository if query_repository is not None else TenantQueryRepository()
        )

    def execute(
        self,
        session: Session,
        command: GetTenantDetailsCommand,
    ) -> TenantDetails:
        """Return tenant details or a stable not-found failure."""

        tenant = self._query_repository.get_tenant(
            session,
            command.tenant_id,
        )

        if tenant is None:
            raise TenantNotFoundError()

        return TenantDetails(
            id=tenant.id,
            name=tenant.name,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
            disabled_at=tenant.disabled_at,
        )


class ListTenantMembershipsService:
    """List memberships owned by one trusted tenant."""

    def __init__(
        self,
        query_repository: TenantQueryRepository | None = None,
    ) -> None:
        self._query_repository = (
            query_repository if query_repository is not None else TenantQueryRepository()
        )

    def execute(
        self,
        session: Session,
        command: ListTenantMembershipsCommand,
    ) -> tuple[TenantMembership, ...]:
        """Return all active and disabled tenant memberships."""

        memberships = self._query_repository.list_memberships(
            session,
            command.tenant_id,
        )

        return tuple(
            TenantMembership(
                id=membership.id,
                tenant_id=membership.tenant_id,
                user_id=membership.user_id,
                role=membership.role,
                status=membership.status,
                created_at=membership.created_at,
                updated_at=membership.updated_at,
                disabled_at=membership.disabled_at,
            )
            for membership in memberships
        )
