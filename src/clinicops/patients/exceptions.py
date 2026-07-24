class PatientError(Exception):
    """Base class for patient-domain failures."""


class PatientNotFoundError(PatientError):
    """Raised when a patient is not visible within the authorized tenant."""

    def __init__(self) -> None:
        super().__init__("Patient was not found in the tenant.")


class PatientAlreadyArchivedError(PatientError):
    """Raised when an archive transition targets an archived patient."""

    def __init__(self) -> None:
        super().__init__("Patient is already archived.")


class PatientNotArchivedError(PatientError):
    """Raised when a restore transition targets an active patient."""

    def __init__(self) -> None:
        super().__init__("Patient is not archived.")


class PatientVersionConflictError(PatientError):
    """Raised when a mutation uses a stale patient version."""

    def __init__(self) -> None:
        super().__init__("Patient version conflict.")


class PatientExternalReferenceConflictError(PatientError):
    """Raised when a tenant-local external reference is already in use."""

    def __init__(self) -> None:
        super().__init__("A patient with the same external reference already exists in the tenant.")


class PatientInvalidDateOfBirthError(PatientError):
    """Raised when a patient's date of birth is in the future."""

    def __init__(self) -> None:
        super().__init__("Patient date of birth cannot be in the future.")


class PatientInvalidUpdateError(PatientError):
    """Raised when a patient patch cannot produce a valid mutation."""

    def __init__(self) -> None:
        super().__init__("Patient update is invalid.")


class PatientInvalidCursorError(PatientError):
    """Raised when a patient pagination cursor is malformed."""

    def __init__(self) -> None:
        super().__init__("Patient cursor is invalid.")


__all__ = [
    "PatientAlreadyArchivedError",
    "PatientError",
    "PatientExternalReferenceConflictError",
    "PatientInvalidCursorError",
    "PatientInvalidDateOfBirthError",
    "PatientInvalidUpdateError",
    "PatientNotArchivedError",
    "PatientNotFoundError",
    "PatientVersionConflictError",
]
