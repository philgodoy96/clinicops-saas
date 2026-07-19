from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from clinicops.tenancy.exceptions import (
    InvalidOwnershipTransferError,
    MembershipDisabledError,
    MembershipNotFoundError,
    TenantDisabledError,
    TenantNotFoundError,
    TenantOwnershipConflictError,
)
from clinicops.tenancy.models import (
    MembershipStatus,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.repository import TenantRepository


@dataclass(frozen=True, slots=True)
class TransferTenantOwnershipCommand:
    """Input required to transfer tenant ownership."""

    tenant_id: UUID
    expected_current_owner_user_id: UUID
    new_owner_user_id: UUID


@dataclass(frozen=True, slots=True)
class TransferredTenantOwnership:
    """Public application result of tenant ownership transfer."""

    tenant_id: UUID
    previous_owner_user_id: UUID
    new_owner_user_id: UUID


class TransferTenantOwnershipService:
    """Transfer tenant ownership inside the caller's transaction."""

    def __init__(
        self,
        repository: TenantRepository | None = None,
    ) -> None:
        self._repository = repository if repository is not None else TenantRepository()

    def execute(
        self,
        session: Session,
        command: TransferTenantOwnershipCommand,
    ) -> TransferredTenantOwnership:
        """Transfer ownership without committing the transaction."""

        tenant = self._repository.get_by_id_for_update(
            session,
            command.tenant_id,
        )

        if tenant is None:
            raise TenantNotFoundError()

        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantDisabledError()

        current_owner = self._repository.get_active_owner_for_update(
            session,
            tenant.id,
        )

        if current_owner is None or current_owner.user_id != command.expected_current_owner_user_id:
            raise TenantOwnershipConflictError()

        if command.new_owner_user_id == current_owner.user_id:
            raise InvalidOwnershipTransferError()

        target_membership = self._repository.get_membership_for_update(
            session,
            tenant.id,
            command.new_owner_user_id,
        )

        if target_membership is None:
            raise MembershipNotFoundError()

        if target_membership.status is not MembershipStatus.ACTIVE:
            raise MembershipDisabledError()

        if target_membership.role not in {
            TenantRole.ADMIN,
            TenantRole.STAFF,
        }:
            raise InvalidOwnershipTransferError()

        previous_owner_user_id = current_owner.user_id

        current_owner.role = TenantRole.ADMIN
        self._repository.flush(session)

        target_membership.role = TenantRole.OWNER
        self._repository.flush(session)

        return TransferredTenantOwnership(
            tenant_id=tenant.id,
            previous_owner_user_id=previous_owner_user_id,
            new_owner_user_id=target_membership.user_id,
        )
