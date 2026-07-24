from datetime import date
from typing import NoReturn

from clinicops.patients.contracts import (
    PatientRecord,
    UpdatePatientCommand,
    UpdatedPatient,
)
from clinicops.patients.enums import (
    PatientMutableField,
    PatientStatus,
)
from clinicops.patients.exceptions import (
    PatientInvalidUpdateError,
    PatientNotFoundError,
    PatientVersionConflictError,
)
from clinicops.patients.repositories.patient_repository import (
    PatientRepository,
)
from clinicops.patients.validation import (
    normalize_email,
    normalize_external_reference,
    normalize_full_name,
    normalize_phone,
    validate_date_of_birth,
    validate_update_fields,
)

_MUTABLE_FIELD_ORDER = (
    PatientMutableField.FULL_NAME,
    PatientMutableField.DATE_OF_BIRTH,
    PatientMutableField.EMAIL,
    PatientMutableField.PHONE,
    PatientMutableField.EXTERNAL_REFERENCE,
)


class UpdatePatientService:
    """Apply validated optimistic updates to active patients."""

    def __init__(
        self,
        repository: PatientRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: UpdatePatientCommand,
    ) -> UpdatedPatient:
        """Update explicitly supplied fields at the observed version."""

        current = self._repository.get_by_id_for_tenant(
            tenant_id=command.tenant_id,
            patient_id=command.patient_id,
        )
        if current is None:
            raise PatientNotFoundError

        _require_current_version(
            current,
            expected_version=command.expected_version,
        )
        _require_active_patient(current)

        requested_fields = validate_update_fields(command.fields_to_update)
        normalized_values = _normalize_requested_values(
            command,
            requested_fields,
        )
        changed_fields = tuple(
            field
            for field in _MUTABLE_FIELD_ORDER
            if field in requested_fields
            and normalized_values[field] != _current_value(current, field)
        )
        if not changed_fields:
            raise PatientInvalidUpdateError

        updated = self._repository.update_for_tenant(
            tenant_id=command.tenant_id,
            patient_id=command.patient_id,
            expected_version=command.expected_version,
            fields_to_update=frozenset(changed_fields),
            full_name=_string_value(
                normalized_values,
                PatientMutableField.FULL_NAME,
            ),
            date_of_birth=_date_value(
                normalized_values,
                PatientMutableField.DATE_OF_BIRTH,
            ),
            email=_string_value(
                normalized_values,
                PatientMutableField.EMAIL,
            ),
            phone=_string_value(
                normalized_values,
                PatientMutableField.PHONE,
            ),
            external_reference=_string_value(
                normalized_values,
                PatientMutableField.EXTERNAL_REFERENCE,
            ),
        )
        if updated is None:
            _raise_failed_update(
                repository=self._repository,
                command=command,
            )

        return UpdatedPatient(
            patient=updated,
            changed_fields=changed_fields,
        )


def _normalize_requested_values(
    command: UpdatePatientCommand,
    requested_fields: frozenset[PatientMutableField],
) -> dict[PatientMutableField, str | date | None]:
    values: dict[PatientMutableField, str | date | None] = {}

    if PatientMutableField.FULL_NAME in requested_fields:
        if command.full_name is None:
            raise PatientInvalidUpdateError
        values[PatientMutableField.FULL_NAME] = normalize_full_name(command.full_name)

    if PatientMutableField.DATE_OF_BIRTH in requested_fields:
        values[PatientMutableField.DATE_OF_BIRTH] = validate_date_of_birth(command.date_of_birth)

    if PatientMutableField.EMAIL in requested_fields:
        values[PatientMutableField.EMAIL] = normalize_email(command.email)

    if PatientMutableField.PHONE in requested_fields:
        values[PatientMutableField.PHONE] = normalize_phone(command.phone)

    if PatientMutableField.EXTERNAL_REFERENCE in requested_fields:
        values[PatientMutableField.EXTERNAL_REFERENCE] = normalize_external_reference(
            command.external_reference
        )

    return values


def _current_value(
    patient: PatientRecord,
    field: PatientMutableField,
) -> str | date | None:
    if field is PatientMutableField.FULL_NAME:
        return patient.full_name
    if field is PatientMutableField.DATE_OF_BIRTH:
        return patient.date_of_birth
    if field is PatientMutableField.EMAIL:
        return patient.email
    if field is PatientMutableField.PHONE:
        return patient.phone
    if field is PatientMutableField.EXTERNAL_REFERENCE:
        return patient.external_reference

    raise PatientInvalidUpdateError


def _string_value(
    values: dict[PatientMutableField, str | date | None],
    field: PatientMutableField,
) -> str | None:
    value = values.get(field)
    if value is None or isinstance(value, str):
        return value

    raise PatientInvalidUpdateError


def _date_value(
    values: dict[PatientMutableField, str | date | None],
    field: PatientMutableField,
) -> date | None:
    value = values.get(field)
    if value is None or isinstance(value, date):
        return value

    raise PatientInvalidUpdateError


def _require_current_version(
    patient: PatientRecord,
    *,
    expected_version: int,
) -> None:
    if patient.version != expected_version:
        raise PatientVersionConflictError


def _require_active_patient(patient: PatientRecord) -> None:
    if patient.status is not PatientStatus.ACTIVE:
        raise PatientInvalidUpdateError


def _raise_failed_update(
    *,
    repository: PatientRepository,
    command: UpdatePatientCommand,
) -> NoReturn:
    latest = repository.get_by_id_for_tenant(
        tenant_id=command.tenant_id,
        patient_id=command.patient_id,
    )
    if latest is None:
        raise PatientNotFoundError

    _require_current_version(
        latest,
        expected_version=command.expected_version,
    )
    _require_active_patient(latest)

    raise PatientInvalidUpdateError


__all__ = ["UpdatePatientService"]
