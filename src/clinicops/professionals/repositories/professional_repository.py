from collections.abc import Mapping
from typing import NoReturn
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update

from clinicops.professionals.contracts import (
    ProfessionalCursor,
    ProfessionalPage,
    ProfessionalRecord,
)
from clinicops.professionals.enums import (
    ProfessionalListStatus,
    ProfessionalMutableField,
    ProfessionalStatus,
)
from clinicops.professionals.exceptions import (
    ProfessionalExternalReferenceConflictError,
    ProfessionalMembershipLinkConflictError,
)
from clinicops.professionals.models import Professional
from clinicops.tenancy.models import Membership, MembershipStatus

_EXTERNAL_REFERENCE_CONSTRAINT = "uq_professionals_tenant_external_reference"
_MEMBERSHIP_LINK_CONSTRAINT = "uq_professionals_membership_id"
_LIKE_ESCAPE_CHARACTER = "\\"


class ProfessionalRepository:
    """Tenant-scoped persistence operations for professional profiles."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, professional: Professional) -> None:
        """Attach a new professional to the current transaction."""

        self._session.add(professional)

    def flush(self) -> None:
        """Flush pending professional changes and translate known conflicts."""

        try:
            self._session.flush()
        except IntegrityError as error:
            self._raise_translated_integrity_error(error)

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        """Return one professional only when it belongs to the tenant."""

        statement = select(Professional).where(
            Professional.tenant_id == tenant_id,
            Professional.id == professional_id,
        )
        professional = self._session.execute(statement).scalar_one_or_none()
        return _to_record(professional) if professional is not None else None

    def get_linked_by_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> ProfessionalRecord | None:
        """Return the professional linked to one tenant membership."""

        statement = select(Professional).where(
            Professional.tenant_id == tenant_id,
            Professional.membership_id == membership_id,
        )
        professional = self._session.execute(statement).scalar_one_or_none()
        return _to_record(professional) if professional is not None else None

    def get_membership_status_for_tenant_for_update(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> MembershipStatus | None:
        """Return and lock one tenant membership status for linking."""

        statement = (
            select(Membership.status)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.id == membership_id,
            )
            .with_for_update()
        )
        return self._session.execute(statement).scalar_one_or_none()

    def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        limit: int,
        status: ProfessionalListStatus,
        search: str | None = None,
        cursor: ProfessionalCursor | None = None,
    ) -> ProfessionalPage:
        """Return one descending keyset page of tenant-owned professionals."""

        statement = select(Professional).where(Professional.tenant_id == tenant_id)

        if status is ProfessionalListStatus.ACTIVE:
            statement = statement.where(Professional.status == ProfessionalStatus.ACTIVE)
        elif status is ProfessionalListStatus.ARCHIVED:
            statement = statement.where(Professional.status == ProfessionalStatus.ARCHIVED)

        if search is not None:
            escaped_search = _escape_like_pattern(search)
            pattern = f"%{escaped_search}%"
            statement = statement.where(
                or_(
                    Professional.full_name.ilike(
                        pattern,
                        escape=_LIKE_ESCAPE_CHARACTER,
                    ),
                    Professional.specialty.ilike(
                        pattern,
                        escape=_LIKE_ESCAPE_CHARACTER,
                    ),
                    Professional.registration_number.ilike(
                        pattern,
                        escape=_LIKE_ESCAPE_CHARACTER,
                    ),
                    Professional.email.ilike(
                        pattern,
                        escape=_LIKE_ESCAPE_CHARACTER,
                    ),
                    Professional.phone.ilike(
                        pattern,
                        escape=_LIKE_ESCAPE_CHARACTER,
                    ),
                    Professional.external_reference.ilike(
                        pattern,
                        escape=_LIKE_ESCAPE_CHARACTER,
                    ),
                )
            )

        if cursor is not None:
            statement = statement.where(
                or_(
                    Professional.created_at < cursor.created_at,
                    and_(
                        Professional.created_at == cursor.created_at,
                        Professional.id < cursor.professional_id,
                    ),
                )
            )

        statement = statement.order_by(
            Professional.created_at.desc(),
            Professional.id.desc(),
        ).limit(limit + 1)

        professionals = tuple(self._session.execute(statement).scalars())
        has_next_page = len(professionals) > limit
        visible_professionals = professionals[:limit]
        records = tuple(_to_record(item) for item in visible_professionals)

        next_cursor = None
        if has_next_page and records:
            last_record = records[-1]
            next_cursor = ProfessionalCursor(
                created_at=last_record.created_at,
                professional_id=last_record.id,
            )

        return ProfessionalPage(
            items=records,
            next_cursor=next_cursor,
        )

    def update_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
        values: Mapping[ProfessionalMutableField, object],
    ) -> ProfessionalRecord | None:
        """Atomically update an active professional at the expected version."""

        persisted_values = {field.value: value for field, value in values.items()}
        statement = (
            update(Professional)
            .where(
                Professional.tenant_id == tenant_id,
                Professional.id == professional_id,
                Professional.status == ProfessionalStatus.ACTIVE,
                Professional.version == expected_version,
            )
            .values(
                **persisted_values,
                version=Professional.version + 1,
                updated_at=func.now(),
            )
            .returning(Professional)
        )

        return self._execute_mutation(statement)

    def archive_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        """Atomically archive an active professional at the expected version."""

        statement = (
            update(Professional)
            .where(
                Professional.tenant_id == tenant_id,
                Professional.id == professional_id,
                Professional.status == ProfessionalStatus.ACTIVE,
                Professional.version == expected_version,
            )
            .values(
                status=ProfessionalStatus.ARCHIVED,
                version=Professional.version + 1,
                updated_at=func.now(),
            )
            .returning(Professional)
        )

        return self._execute_mutation(statement)

    def restore_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        """Atomically restore an archived professional at the expected version."""

        statement = (
            update(Professional)
            .where(
                Professional.tenant_id == tenant_id,
                Professional.id == professional_id,
                Professional.status == ProfessionalStatus.ARCHIVED,
                Professional.version == expected_version,
            )
            .values(
                status=ProfessionalStatus.ACTIVE,
                version=Professional.version + 1,
                updated_at=func.now(),
            )
            .returning(Professional)
        )

        return self._execute_mutation(statement)

    def link_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        membership_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        """Atomically link an active, currently unlinked professional."""

        statement = (
            update(Professional)
            .where(
                Professional.tenant_id == tenant_id,
                Professional.id == professional_id,
                Professional.status == ProfessionalStatus.ACTIVE,
                Professional.version == expected_version,
                Professional.membership_id.is_(None),
            )
            .values(
                membership_id=membership_id,
                version=Professional.version + 1,
                updated_at=func.now(),
            )
            .returning(Professional)
        )

        return self._execute_mutation(statement)

    def unlink_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
    ) -> ProfessionalRecord | None:
        """Atomically unlink an active professional at the expected version."""

        statement = (
            update(Professional)
            .where(
                Professional.tenant_id == tenant_id,
                Professional.id == professional_id,
                Professional.status == ProfessionalStatus.ACTIVE,
                Professional.version == expected_version,
                Professional.membership_id.is_not(None),
            )
            .values(
                membership_id=None,
                version=Professional.version + 1,
                updated_at=func.now(),
            )
            .returning(Professional)
        )

        return self._execute_mutation(statement)

    def unlink_by_membership_for_tenant(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> ProfessionalRecord | None:
        """Clear a professional link during tenant Membership removal."""

        statement = (
            update(Professional)
            .where(
                Professional.tenant_id == tenant_id,
                Professional.membership_id == membership_id,
            )
            .values(
                membership_id=None,
                version=Professional.version + 1,
                updated_at=func.now(),
            )
            .returning(Professional)
        )

        return self._execute_mutation(statement)

    def _execute_mutation(
        self,
        statement: Update,
    ) -> ProfessionalRecord | None:
        try:
            professional = self._session.execute(statement).scalar_one_or_none()
        except IntegrityError as error:
            self._raise_translated_integrity_error(error)

        return _to_record(professional) if professional is not None else None

    @staticmethod
    def _raise_translated_integrity_error(
        error: IntegrityError,
    ) -> NoReturn:
        constraint_name = _constraint_name(error)

        if constraint_name == _EXTERNAL_REFERENCE_CONSTRAINT:
            raise ProfessionalExternalReferenceConflictError from error

        if constraint_name == _MEMBERSHIP_LINK_CONSTRAINT:
            raise ProfessionalMembershipLinkConflictError from error

        raise error


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _escape_like_pattern(value: str) -> str:
    return (
        value.replace(
            _LIKE_ESCAPE_CHARACTER,
            _LIKE_ESCAPE_CHARACTER * 2,
        )
        .replace("%", f"{_LIKE_ESCAPE_CHARACTER}%")
        .replace("_", f"{_LIKE_ESCAPE_CHARACTER}_")
    )


def _to_record(professional: Professional) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=professional.id,
        tenant_id=professional.tenant_id,
        membership_id=professional.membership_id,
        full_name=professional.full_name,
        specialty=professional.specialty,
        registration_number=professional.registration_number,
        registration_region=professional.registration_region,
        email=professional.email,
        phone=professional.phone,
        external_reference=professional.external_reference,
        status=professional.status,
        version=professional.version,
        created_at=professional.created_at,
        updated_at=professional.updated_at,
    )


__all__ = ["ProfessionalRepository"]
