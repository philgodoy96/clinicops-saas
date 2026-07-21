from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from clinicops.tenancy.models import Membership, Tenant


@dataclass(frozen=True, slots=True)
class LockedMembershipAdministrationState:
    """Actor and target memberships locked in deterministic order."""

    actor: Membership | None
    target: Membership | None


class MembershipAdministrationRepository:
    """Persistence operations for membership administration workflows."""

    def get_tenant_for_update(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> Tenant | None:
        """Lock one tenant for an administrative transition."""

        statement = (
            select(Tenant)
            .where(Tenant.id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def get_actor_and_target_for_update(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        actor_user_id: UUID,
        target_membership_id: UUID,
    ) -> LockedMembershipAdministrationState:
        """Lock relevant memberships by stable primary-key order."""

        statement = (
            select(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                or_(
                    Membership.user_id == actor_user_id,
                    Membership.id == target_membership_id,
                ),
            )
            .order_by(Membership.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        memberships = list(session.scalars(statement).all())

        actor = next(
            (membership for membership in memberships if membership.user_id == actor_user_id),
            None,
        )
        target = next(
            (membership for membership in memberships if membership.id == target_membership_id),
            None,
        )

        return LockedMembershipAdministrationState(
            actor=actor,
            target=target,
        )

    def flush_and_refresh(
        self,
        session: Session,
        membership: Membership,
    ) -> None:
        """Flush a membership mutation and refresh server timestamps."""

        session.flush()
        session.refresh(membership)

    def delete_and_flush(
        self,
        session: Session,
        membership: Membership,
    ) -> None:
        """Delete one membership without committing the transaction."""

        session.delete(membership)
        session.flush()
