from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from psycopg.errors import UniqueViolation
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.models import AuditLogEntry
from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.tenancy.exceptions import TenantOwnershipConflictError
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
)
from clinicops.tenancy.repository import TenantRepository
from clinicops.tenancy.services.transfer_ownership import (
    TransferTenantOwnershipCommand,
    TransferTenantOwnershipService,
)


def audit_context(
    *,
    user_id: UUID,
    role: str = TenantRole.OWNER.value,
) -> AuditRecordingContext:
    """Build one immutable HTTP audit context."""

    return AuditRecordingContext.http_user(
        user_id=user_id,
        role=role,
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
    )


class SimulatedOwnershipTransferError(RuntimeError):
    """Represent a failure after the current owner is demoted."""


class CoordinatedTenantRepository(TenantRepository):
    """Coordinate transfer attempts before they acquire the tenant lock."""

    def __init__(self, lock_barrier: Barrier) -> None:
        self._lock_barrier = lock_barrier

    def get_by_id_for_update(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> Tenant | None:
        self._lock_barrier.wait(timeout=5)
        return super().get_by_id_for_update(session, tenant_id)


class FailingSecondFlushTenantRepository(TenantRepository):
    """Fail after the owner demotion has been flushed."""

    def __init__(self) -> None:
        self._flush_calls = 0

    def flush(self, session: Session) -> None:
        self._flush_calls += 1

        if self._flush_calls == 2:
            raise SimulatedOwnershipTransferError

        super().flush(session)


@dataclass(frozen=True, slots=True)
class PersistedOwnershipFixture:
    """Identifiers for a committed ownership transfer test aggregate."""

    tenant_id: UUID
    owner_user_id: UUID
    first_target_user_id: UUID
    second_target_user_id: UUID


def persist_ownership_fixture() -> PersistedOwnershipFixture:
    """Persist a tenant with one owner and two eligible targets."""

    owner_user = User(email=f"owner-{uuid4()}@example.com")
    first_target_user = User(email=f"first-target-{uuid4()}@example.com")
    second_target_user = User(email=f"second-target-{uuid4()}@example.com")
    tenant = Tenant(name=f"Concurrent Ownership Clinic {uuid4()}")

    tenant.memberships.extend(
        [
            Membership(
                user=owner_user,
                role=TenantRole.OWNER,
            ),
            Membership(
                user=first_target_user,
                role=TenantRole.STAFF,
            ),
            Membership(
                user=second_target_user,
                role=TenantRole.STAFF,
            ),
        ]
    )

    with Session(get_engine()) as session:
        session.add_all(
            [
                tenant,
                owner_user,
                first_target_user,
                second_target_user,
            ]
        )
        session.flush()

        fixture = PersistedOwnershipFixture(
            tenant_id=tenant.id,
            owner_user_id=owner_user.id,
            first_target_user_id=first_target_user.id,
            second_target_user_id=second_target_user.id,
        )
        session.commit()

    return fixture


def delete_ownership_fixture(
    fixture: PersistedOwnershipFixture,
) -> None:
    """Delete a committed test aggregate and its global users."""

    with Session(get_engine()) as session:
        session.execute(delete(AuditLogEntry).where(AuditLogEntry.tenant_id == fixture.tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
        session.execute(
            delete(User).where(
                User.id.in_(
                    [
                        fixture.owner_user_id,
                        fixture.first_target_user_id,
                        fixture.second_target_user_id,
                    ]
                )
            )
        )
        session.commit()


def transfer_in_independent_transaction(
    fixture: PersistedOwnershipFixture,
    new_owner_user_id: UUID,
    repository: TenantRepository,
) -> tuple[str, UUID]:
    """Attempt one ownership transfer in an independent transaction."""

    service = TransferTenantOwnershipService(repository=repository)

    with Session(get_engine()) as session:
        try:
            service.execute(
                session,
                TransferTenantOwnershipCommand(
                    tenant_id=fixture.tenant_id,
                    expected_current_owner_user_id=fixture.owner_user_id,
                    new_owner_user_id=new_owner_user_id,
                    audit_context=audit_context(user_id=fixture.owner_user_id),
                ),
            )
            session.commit()
        except TenantOwnershipConflictError:
            session.rollback()
            return "conflict", new_owner_user_id

    return "transferred", new_owner_user_id


def load_roles(
    fixture: PersistedOwnershipFixture,
) -> dict[UUID, TenantRole]:
    """Load membership roles for one committed tenant."""

    with Session(get_engine()) as session:
        memberships = list(
            session.scalars(select(Membership).where(Membership.tenant_id == fixture.tenant_id))
        )

    return {membership.user_id: membership.role for membership in memberships}


def count_active_owners(
    fixture: PersistedOwnershipFixture,
) -> int:
    """Count active owner memberships for one committed tenant."""

    with Session(get_engine()) as session:
        memberships = list(
            session.scalars(select(Membership).where(Membership.tenant_id == fixture.tenant_id))
        )

    return sum(
        membership.role is TenantRole.OWNER and membership.status is MembershipStatus.ACTIVE
        for membership in memberships
    )


def test_concurrent_transfers_serialize_and_allow_one_winner() -> None:
    fixture = persist_ownership_fixture()
    repository = CoordinatedTenantRepository(Barrier(2))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                transfer_in_independent_transaction,
                fixture,
                fixture.first_target_user_id,
                repository,
            )
            second_future = executor.submit(
                transfer_in_independent_transaction,
                fixture,
                fixture.second_target_user_id,
                repository,
            )

            results = [
                first_future.result(timeout=10),
                second_future.result(timeout=10),
            ]

        statuses = sorted(status for status, _ in results)

        assert statuses == ["conflict", "transferred"]

        winning_user_id = next(user_id for status, user_id in results if status == "transferred")
        losing_user_id = next(user_id for status, user_id in results if status == "conflict")
        roles = load_roles(fixture)

        assert roles[fixture.owner_user_id] is TenantRole.ADMIN
        assert roles[winning_user_id] is TenantRole.OWNER
        assert roles[losing_user_id] is TenantRole.STAFF
        assert count_active_owners(fixture) == 1
    finally:
        delete_ownership_fixture(fixture)


def test_failure_after_demotion_rolls_back_original_owner() -> None:
    fixture = persist_ownership_fixture()
    service = TransferTenantOwnershipService(
        repository=FailingSecondFlushTenantRepository(),
    )

    try:
        with Session(get_engine()) as session:
            with pytest.raises(SimulatedOwnershipTransferError):
                service.execute(
                    session,
                    TransferTenantOwnershipCommand(
                        tenant_id=fixture.tenant_id,
                        expected_current_owner_user_id=fixture.owner_user_id,
                        new_owner_user_id=fixture.first_target_user_id,
                        audit_context=audit_context(user_id=fixture.owner_user_id),
                    ),
                )

            session.rollback()

        roles = load_roles(fixture)

        assert roles[fixture.owner_user_id] is TenantRole.OWNER
        assert roles[fixture.first_target_user_id] is TenantRole.STAFF
        assert count_active_owners(fixture) == 1
    finally:
        delete_ownership_fixture(fixture)


def test_successful_transfer_remains_owned_by_calling_transaction() -> None:
    fixture = persist_ownership_fixture()
    service = TransferTenantOwnershipService()

    try:
        with Session(get_engine()) as session:
            service.execute(
                session,
                TransferTenantOwnershipCommand(
                    tenant_id=fixture.tenant_id,
                    expected_current_owner_user_id=fixture.owner_user_id,
                    new_owner_user_id=fixture.first_target_user_id,
                    audit_context=audit_context(user_id=fixture.owner_user_id),
                ),
            )
            session.rollback()

        roles = load_roles(fixture)

        assert roles[fixture.owner_user_id] is TenantRole.OWNER
        assert roles[fixture.first_target_user_id] is TenantRole.STAFF
        assert count_active_owners(fixture) == 1
    finally:
        delete_ownership_fixture(fixture)


def test_partial_unique_index_rejects_second_active_owner() -> None:
    fixture = persist_ownership_fixture()

    try:
        with Session(get_engine()) as session:
            target_membership = session.scalar(
                select(Membership).where(
                    Membership.tenant_id == fixture.tenant_id,
                    Membership.user_id == fixture.first_target_user_id,
                )
            )

            assert target_membership is not None

            target_membership.role = TenantRole.OWNER

            with pytest.raises(IntegrityError) as exception_info:
                session.flush()

            original_exception = exception_info.value.orig

            assert isinstance(original_exception, UniqueViolation)
            assert (
                original_exception.diag.constraint_name
                == "uq_memberships_one_active_owner_per_tenant"
            )

            session.rollback()

        roles = load_roles(fixture)

        assert roles[fixture.owner_user_id] is TenantRole.OWNER
        assert roles[fixture.first_target_user_id] is TenantRole.STAFF
        assert count_active_owners(fixture) == 1
    finally:
        delete_ownership_fixture(fixture)
