from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.patients.contracts import (
    ListPatientsCommand,
    PatientCursor,
    PatientPage,
    PatientRecord,
)
from clinicops.patients.enums import (
    PatientListStatus,
    PatientMutableField,
    PatientStatus,
)
from clinicops.patients.exceptions import (
    PatientExternalReferenceConflictError,
)
from clinicops.patients.models import Patient
from clinicops.patients.validation import SEARCH_MAX_LENGTH

_MIN_PAGE_LIMIT = 1
_MAX_PAGE_LIMIT = 100
_EXTERNAL_REFERENCE_CONSTRAINT = "uq_patients_tenant_external_reference"


class PatientRepository:
    """Persist and query patients within explicit tenant boundaries."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, patient: Patient) -> None:
        """Attach a new patient to the current transaction."""

        self._session.add(patient)

    def flush(self) -> None:
        """Flush pending patient changes without owning the transaction."""

        try:
            self._session.flush()
        except IntegrityError as exc:
            _raise_external_reference_conflict(exc)
            raise

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
    ) -> PatientRecord | None:
        """Return one patient only when it belongs to the tenant."""

        statement = (
            select(Patient)
            .where(
                Patient.tenant_id == tenant_id,
                Patient.id == patient_id,
            )
            .execution_options(populate_existing=True)
        )
        patient = self._session.scalars(statement).one_or_none()
        if patient is None:
            return None

        return _to_patient_record(patient)

    def list_page_for_tenant(
        self,
        query: ListPatientsCommand,
    ) -> PatientPage:
        """Return one stable tenant-scoped page of patient records."""

        _validate_page_query(query)

        statement = select(Patient).where(
            Patient.tenant_id == query.tenant_id,
        )

        if query.status is not PatientListStatus.ALL:
            statement = statement.where(
                Patient.status == PatientStatus(query.status.value),
            )

        if query.search is not None:
            escaped_search = _escape_like_pattern(query.search)
            pattern = f"%{escaped_search}%"
            statement = statement.where(
                or_(
                    Patient.full_name.ilike(pattern, escape="\\"),
                    Patient.email.ilike(pattern, escape="\\"),
                    Patient.phone.ilike(pattern, escape="\\"),
                    Patient.external_reference.ilike(
                        pattern,
                        escape="\\",
                    ),
                )
            )

        if query.cursor is not None:
            statement = statement.where(
                or_(
                    Patient.created_at < query.cursor.created_at,
                    and_(
                        Patient.created_at == query.cursor.created_at,
                        Patient.id < query.cursor.patient_id,
                    ),
                )
            )

        statement = statement.order_by(
            Patient.created_at.desc(),
            Patient.id.desc(),
        ).limit(query.limit + 1)

        patients = list(self._session.scalars(statement).all())
        has_next_page = len(patients) > query.limit
        page_patients = patients[: query.limit]

        next_cursor: PatientCursor | None = None
        if has_next_page and page_patients:
            last_patient = page_patients[-1]
            next_cursor = PatientCursor(
                created_at=last_patient.created_at,
                patient_id=last_patient.id,
            )

        return PatientPage(
            items=tuple(_to_patient_record(patient) for patient in page_patients),
            next_cursor=next_cursor,
        )

    def update_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
        expected_version: int,
        fields_to_update: frozenset[PatientMutableField],
        full_name: str | None = None,
        date_of_birth: date | None = None,
        email: str | None = None,
        phone: str | None = None,
        external_reference: str | None = None,
    ) -> PatientRecord | None:
        """Atomically update an active patient at the expected version."""

        values: dict[str, Any] = {
            "version": Patient.version + 1,
            "updated_at": func.now(),
        }

        if PatientMutableField.FULL_NAME in fields_to_update:
            values["full_name"] = full_name
        if PatientMutableField.DATE_OF_BIRTH in fields_to_update:
            values["date_of_birth"] = date_of_birth
        if PatientMutableField.EMAIL in fields_to_update:
            values["email"] = email
        if PatientMutableField.PHONE in fields_to_update:
            values["phone"] = phone
        if PatientMutableField.EXTERNAL_REFERENCE in fields_to_update:
            values["external_reference"] = external_reference

        statement = (
            update(Patient)
            .where(
                Patient.tenant_id == tenant_id,
                Patient.id == patient_id,
                Patient.version == expected_version,
                Patient.status == PatientStatus.ACTIVE,
            )
            .values(**values)
            .returning(Patient)
        )

        try:
            patient = self._session.scalars(statement).one_or_none()
        except IntegrityError as exc:
            _raise_external_reference_conflict(exc)
            raise

        if patient is None:
            return None

        return _to_patient_record(patient)

    def archive_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
        expected_version: int,
    ) -> PatientRecord | None:
        """Atomically archive an active patient at the expected version."""

        statement = (
            update(Patient)
            .where(
                Patient.tenant_id == tenant_id,
                Patient.id == patient_id,
                Patient.version == expected_version,
                Patient.status == PatientStatus.ACTIVE,
            )
            .values(
                status=PatientStatus.ARCHIVED,
                version=Patient.version + 1,
                updated_at=func.now(),
            )
            .returning(Patient)
        )
        patient = self._session.scalars(statement).one_or_none()
        if patient is None:
            return None

        return _to_patient_record(patient)

    def restore_for_tenant(
        self,
        *,
        tenant_id: UUID,
        patient_id: UUID,
        expected_version: int,
    ) -> PatientRecord | None:
        """Atomically restore an archived patient at the expected version."""

        statement = (
            update(Patient)
            .where(
                Patient.tenant_id == tenant_id,
                Patient.id == patient_id,
                Patient.version == expected_version,
                Patient.status == PatientStatus.ARCHIVED,
            )
            .values(
                status=PatientStatus.ACTIVE,
                version=Patient.version + 1,
                updated_at=func.now(),
            )
            .returning(Patient)
        )
        patient = self._session.scalars(statement).one_or_none()
        if patient is None:
            return None

        return _to_patient_record(patient)


def _validate_page_query(query: ListPatientsCommand) -> None:
    if not _MIN_PAGE_LIMIT <= query.limit <= _MAX_PAGE_LIMIT:
        raise ValueError("Patient page limit must be between 1 and 100.")

    if query.search is not None and len(query.search) > SEARCH_MAX_LENGTH:
        raise ValueError(f"Patient search must be at most {SEARCH_MAX_LENGTH} characters.")


def _escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _raise_external_reference_conflict(
    error: IntegrityError,
) -> None:
    if _constraint_name(error) == _EXTERNAL_REFERENCE_CONSTRAINT:
        raise PatientExternalReferenceConflictError from error


def _to_patient_record(patient: Patient) -> PatientRecord:
    return PatientRecord(
        id=patient.id,
        tenant_id=patient.tenant_id,
        full_name=patient.full_name,
        date_of_birth=patient.date_of_birth,
        email=patient.email,
        phone=patient.phone,
        external_reference=patient.external_reference,
        status=patient.status,
        version=patient.version,
        created_at=patient.created_at,
        updated_at=patient.updated_at,
    )


__all__ = ["PatientRepository"]
