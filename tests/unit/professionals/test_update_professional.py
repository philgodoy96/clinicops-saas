from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from clinicops.professionals.contracts import (
    ProfessionalRecord,
    UpdateProfessionalCommand,
)
from clinicops.professionals.enums import (
    ProfessionalMutableField,
    ProfessionalStatus,
)
from clinicops.professionals.exceptions import (
    ProfessionalExternalReferenceConflictError,
    ProfessionalInvalidUpdateError,
    ProfessionalNotFoundError,
    ProfessionalVersionConflictError,
)
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.update_professional import (
    UpdateProfessionalService,
)

_RECORDED_AT = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)


class RecordingProfessionalRepository:
    """Record optimistic professional update interactions."""

    def __init__(
        self,
        *,
        reads: list[ProfessionalRecord | None],
        updated: ProfessionalRecord | None = None,
        update_error: Exception | None = None,
    ) -> None:
        self._reads = iter(reads)
        self._updated = updated
        self._update_error = update_error
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.update_calls: list[
            tuple[
                UUID,
                UUID,
                int,
                dict[ProfessionalMutableField, object],
            ]
        ] = []

    def get_by_id_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
    ) -> ProfessionalRecord | None:
        self.get_calls.append((tenant_id, professional_id))
        return next(self._reads)

    def update_for_tenant(
        self,
        *,
        tenant_id: UUID,
        professional_id: UUID,
        expected_version: int,
        values: Mapping[ProfessionalMutableField, object],
    ) -> ProfessionalRecord | None:
        self.update_calls.append(
            (
                tenant_id,
                professional_id,
                expected_version,
                dict(values),
            )
        )
        if self._update_error is not None:
            raise self._update_error
        return self._updated


def _repository(
    recording: RecordingProfessionalRepository,
) -> ProfessionalRepository:
    return cast(ProfessionalRepository, recording)


def _professional_record(
    *,
    tenant_id: UUID | None = None,
    professional_id: UUID | None = None,
    status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
    version: int = 1,
    full_name: str = "Morgan Reed",
    specialty: str | None = "Dentistry",
    registration_number: str | None = "DDS-48291",
    registration_region: str | None = "CA",
    email: str | None = "morgan@example.com",
    phone: str | None = "+1-202-555-0130",
    external_reference: str | None = "PROVIDER-100",
) -> ProfessionalRecord:
    return ProfessionalRecord(
        id=professional_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        membership_id=None,
        full_name=full_name,
        specialty=specialty,
        registration_number=registration_number,
        registration_region=registration_region,
        email=email,
        phone=phone,
        external_reference=external_reference,
        status=status,
        version=version,
        created_at=_RECORDED_AT,
        updated_at=_RECORDED_AT,
    )


def _command(
    current: ProfessionalRecord,
    *,
    expected_version: int | None = None,
    fields_to_update: frozenset[ProfessionalMutableField],
    full_name: str | None = None,
    specialty: str | None = None,
    registration_number: str | None = None,
    registration_region: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    external_reference: str | None = None,
) -> UpdateProfessionalCommand:
    return UpdateProfessionalCommand(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        expected_version=(current.version if expected_version is None else expected_version),
        fields_to_update=fields_to_update,
        full_name=full_name,
        specialty=specialty,
        registration_number=registration_number,
        registration_region=registration_region,
        email=email,
        phone=phone,
        external_reference=external_reference,
    )


def test_update_professional_normalizes_and_updates_changed_fields() -> None:
    current = _professional_record()
    updated = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
        full_name="Morgan A. Reed",
        specialty="Orthodontics",
        registration_region="NY",
        email="new@example.com",
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        updated=updated,
    )
    service = UpdateProfessionalService(_repository(recording))
    command = _command(
        current,
        fields_to_update=frozenset(
            {
                ProfessionalMutableField.EMAIL,
                ProfessionalMutableField.REGISTRATION_REGION,
                ProfessionalMutableField.SPECIALTY,
                ProfessionalMutableField.FULL_NAME,
            }
        ),
        full_name="  Morgan A. Reed  ",
        specialty="  Orthodontics  ",
        registration_region="  ny  ",
        email="  NEW@EXAMPLE.COM  ",
    )

    result = service.execute(command)

    assert result.professional is updated
    assert result.changed_fields == (
        ProfessionalMutableField.FULL_NAME,
        ProfessionalMutableField.SPECIALTY,
        ProfessionalMutableField.REGISTRATION_REGION,
        ProfessionalMutableField.EMAIL,
    )
    assert recording.get_calls == [(current.tenant_id, current.id)]
    assert recording.update_calls == [
        (
            current.tenant_id,
            current.id,
            1,
            {
                ProfessionalMutableField.FULL_NAME: "Morgan A. Reed",
                ProfessionalMutableField.SPECIALTY: "Orthodontics",
                ProfessionalMutableField.REGISTRATION_REGION: "NY",
                ProfessionalMutableField.EMAIL: "new@example.com",
            },
        )
    ]


@pytest.mark.parametrize(
    "field",
    [
        ProfessionalMutableField.SPECIALTY,
        ProfessionalMutableField.REGISTRATION_NUMBER,
        ProfessionalMutableField.REGISTRATION_REGION,
        ProfessionalMutableField.EMAIL,
        ProfessionalMutableField.PHONE,
        ProfessionalMutableField.EXTERNAL_REFERENCE,
    ],
)
def test_update_professional_allows_explicit_null_for_nullable_fields(
    field: ProfessionalMutableField,
) -> None:
    current = _professional_record()
    updated = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        updated=updated,
    )
    service = UpdateProfessionalService(_repository(recording))

    result = service.execute(
        _command(
            current,
            fields_to_update=frozenset({field}),
        )
    )

    assert result.changed_fields == (field,)
    assert recording.update_calls[0][3] == {field: None}


def test_update_professional_sends_only_effectively_changed_fields() -> None:
    current = _professional_record()
    updated = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
        email="changed@example.com",
    )
    recording = RecordingProfessionalRepository(
        reads=[current],
        updated=updated,
    )
    service = UpdateProfessionalService(_repository(recording))

    result = service.execute(
        _command(
            current,
            fields_to_update=frozenset(
                {
                    ProfessionalMutableField.FULL_NAME,
                    ProfessionalMutableField.EMAIL,
                }
            ),
            full_name="  Morgan Reed  ",
            email="changed@example.com",
        )
    )

    assert result.changed_fields == (ProfessionalMutableField.EMAIL,)
    assert recording.update_calls[0][3] == {ProfessionalMutableField.EMAIL: "changed@example.com"}


@pytest.mark.parametrize(
    "command_factory",
    [
        lambda current: _command(
            current,
            fields_to_update=frozenset(),
        ),
        lambda current: _command(
            current,
            fields_to_update=frozenset({ProfessionalMutableField.FULL_NAME}),
            full_name=None,
        ),
        lambda current: _command(
            current,
            fields_to_update=frozenset({ProfessionalMutableField.FULL_NAME}),
            full_name="   ",
        ),
        lambda current: _command(
            current,
            fields_to_update=frozenset({ProfessionalMutableField.EMAIL}),
            email="not-an-email",
        ),
        lambda current: _command(
            current,
            fields_to_update=frozenset({ProfessionalMutableField.FULL_NAME}),
            full_name="  Morgan Reed  ",
        ),
    ],
)
def test_update_professional_rejects_invalid_or_no_op_patch(
    command_factory: Callable[[ProfessionalRecord], UpdateProfessionalCommand],
) -> None:
    current = _professional_record()
    recording = RecordingProfessionalRepository(reads=[current])
    service = UpdateProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalInvalidUpdateError):
        service.execute(command_factory(current))

    assert recording.update_calls == []


def test_update_professional_rejects_archived_record() -> None:
    current = _professional_record(status=ProfessionalStatus.ARCHIVED)
    recording = RecordingProfessionalRepository(reads=[current])
    service = UpdateProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalInvalidUpdateError):
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert recording.update_calls == []


def test_update_professional_rejects_stale_version_before_mutation() -> None:
    current = _professional_record(version=4)
    recording = RecordingProfessionalRepository(reads=[current])
    service = UpdateProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(
            _command(
                current,
                expected_version=3,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert recording.update_calls == []


def test_update_professional_raises_not_found_for_invisible_record() -> None:
    tenant_id = uuid4()
    professional_id = uuid4()
    recording = RecordingProfessionalRepository(reads=[None])
    service = UpdateProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalNotFoundError):
        service.execute(
            UpdateProfessionalCommand(
                tenant_id=tenant_id,
                professional_id=professional_id,
                expected_version=1,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert recording.get_calls == [(tenant_id, professional_id)]
    assert recording.update_calls == []


def test_update_professional_classifies_concurrent_winner_as_version_conflict() -> None:
    current = _professional_record(version=1)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        updated=None,
    )
    service = UpdateProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )

    assert recording.get_calls == [
        (current.tenant_id, current.id),
        (current.tenant_id, current.id),
    ]
    assert len(recording.update_calls) == 1


def test_update_professional_classifies_concurrent_archive_as_version_conflict() -> None:
    current = _professional_record(version=1)
    latest = _professional_record(
        tenant_id=current.tenant_id,
        professional_id=current.id,
        status=ProfessionalStatus.ARCHIVED,
        version=2,
    )
    recording = RecordingProfessionalRepository(
        reads=[current, latest],
        updated=None,
    )
    service = UpdateProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalVersionConflictError):
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.SPECIALTY}),
                specialty="Orthodontics",
            )
        )


def test_update_professional_propagates_external_reference_conflict() -> None:
    current = _professional_record()
    error = ProfessionalExternalReferenceConflictError()
    recording = RecordingProfessionalRepository(
        reads=[current],
        update_error=error,
    )
    service = UpdateProfessionalService(_repository(recording))

    with pytest.raises(ProfessionalExternalReferenceConflictError) as captured:
        service.execute(
            _command(
                current,
                fields_to_update=frozenset({ProfessionalMutableField.EXTERNAL_REFERENCE}),
                external_reference="PROVIDER-200",
            )
        )

    assert captured.value is error
    assert len(recording.update_calls) == 1
