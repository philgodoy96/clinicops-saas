from typing import NoReturn

from clinicops.professionals.contracts import (
    ProfessionalRecord,
    UpdatedProfessional,
    UpdateProfessionalCommand,
)
from clinicops.professionals.enums import (
    ProfessionalMutableField,
    ProfessionalStatus,
)
from clinicops.professionals.exceptions import (
    ProfessionalInvalidUpdateError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.validation import (
    normalize_email,
    normalize_external_reference,
    normalize_full_name,
    normalize_phone,
    normalize_registration_number,
    normalize_registration_region,
    normalize_specialty,
    validate_update_fields,
)

_MUTABLE_FIELD_ORDER = (
    ProfessionalMutableField.FULL_NAME,
    ProfessionalMutableField.SPECIALTY,
    ProfessionalMutableField.REGISTRATION_NUMBER,
    ProfessionalMutableField.REGISTRATION_REGION,
    ProfessionalMutableField.EMAIL,
    ProfessionalMutableField.PHONE,
    ProfessionalMutableField.EXTERNAL_REFERENCE,
)


class UpdateProfessionalService:
    """Apply validated optimistic updates to active professionals."""

    def __init__(
        self,
        repository: ProfessionalRepository,
    ) -> None:
        self._repository = repository

    def execute(
        self,
        command: UpdateProfessionalCommand,
    ) -> UpdatedProfessional:
        """Update explicitly supplied fields at the observed version."""

        current = self._repository.get_by_id_for_tenant(
            tenant_id=command.tenant_id,
            professional_id=command.professional_id,
        )
        if current is None:
            raise ProfessionalNotFoundError

        _require_current_version(
            current,
            expected_version=command.expected_version,
        )
        _require_active_professional(current)

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
            raise ProfessionalInvalidUpdateError

        updated = self._repository.update_for_tenant(
            tenant_id=command.tenant_id,
            professional_id=command.professional_id,
            expected_version=command.expected_version,
            values={field: normalized_values[field] for field in changed_fields},
        )
        if updated is None:
            _raise_failed_update(
                repository=self._repository,
                command=command,
            )

        return UpdatedProfessional(
            professional=updated,
            changed_fields=changed_fields,
        )


def _normalize_requested_values(
    command: UpdateProfessionalCommand,
    requested_fields: frozenset[ProfessionalMutableField],
) -> dict[ProfessionalMutableField, str | None]:
    values: dict[ProfessionalMutableField, str | None] = {}

    try:
        if ProfessionalMutableField.FULL_NAME in requested_fields:
            if command.full_name is None:
                raise ProfessionalInvalidUpdateError
            values[ProfessionalMutableField.FULL_NAME] = normalize_full_name(command.full_name)

        if ProfessionalMutableField.SPECIALTY in requested_fields:
            values[ProfessionalMutableField.SPECIALTY] = normalize_specialty(command.specialty)

        if ProfessionalMutableField.REGISTRATION_NUMBER in requested_fields:
            values[ProfessionalMutableField.REGISTRATION_NUMBER] = normalize_registration_number(
                command.registration_number
            )

        if ProfessionalMutableField.REGISTRATION_REGION in requested_fields:
            values[ProfessionalMutableField.REGISTRATION_REGION] = normalize_registration_region(
                command.registration_region
            )

        if ProfessionalMutableField.EMAIL in requested_fields:
            values[ProfessionalMutableField.EMAIL] = normalize_email(command.email)

        if ProfessionalMutableField.PHONE in requested_fields:
            values[ProfessionalMutableField.PHONE] = normalize_phone(command.phone)

        if ProfessionalMutableField.EXTERNAL_REFERENCE in requested_fields:
            values[ProfessionalMutableField.EXTERNAL_REFERENCE] = normalize_external_reference(
                command.external_reference
            )
    except ValueError:
        raise ProfessionalInvalidUpdateError from None

    return values


def _current_value(
    professional: ProfessionalRecord,
    field: ProfessionalMutableField,
) -> str | None:
    if field is ProfessionalMutableField.FULL_NAME:
        return professional.full_name
    if field is ProfessionalMutableField.SPECIALTY:
        return professional.specialty
    if field is ProfessionalMutableField.REGISTRATION_NUMBER:
        return professional.registration_number
    if field is ProfessionalMutableField.REGISTRATION_REGION:
        return professional.registration_region
    if field is ProfessionalMutableField.EMAIL:
        return professional.email
    if field is ProfessionalMutableField.PHONE:
        return professional.phone
    if field is ProfessionalMutableField.EXTERNAL_REFERENCE:
        return professional.external_reference

    raise ProfessionalInvalidUpdateError


def _require_current_version(
    professional: ProfessionalRecord,
    *,
    expected_version: int,
) -> None:
    if professional.version != expected_version:
        raise ProfessionalVersionConflictError


def _require_active_professional(
    professional: ProfessionalRecord,
) -> None:
    if professional.status is not ProfessionalStatus.ACTIVE:
        raise ProfessionalInvalidUpdateError


def _raise_failed_update(
    *,
    repository: ProfessionalRepository,
    command: UpdateProfessionalCommand,
) -> NoReturn:
    latest = repository.get_by_id_for_tenant(
        tenant_id=command.tenant_id,
        professional_id=command.professional_id,
    )
    if latest is None:
        raise ProfessionalNotFoundError

    _require_current_version(
        latest,
        expected_version=command.expected_version,
    )
    _require_active_professional(latest)

    raise ProfessionalInvalidUpdateError


__all__ = ["UpdateProfessionalService"]
