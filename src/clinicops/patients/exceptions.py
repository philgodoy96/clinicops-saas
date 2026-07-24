from clinicops.core.exceptions import ApplicationError


class PatientError(ApplicationError):
    """Base class for expected patient-domain failures."""

    code = "patient_error"
    public_message = "The patient operation could not be completed."


class PatientNotFoundError(PatientError):
    """Raised when a patient is not visible within the authorized tenant."""

    code = "patient_not_found"
    public_message = "The patient was not found."


class PatientAlreadyArchivedError(PatientError):
    """Raised when an archive transition targets an archived patient."""

    code = "patient_already_archived"
    public_message = "The patient is already archived."


class PatientNotArchivedError(PatientError):
    """Raised when a restore transition targets an active patient."""

    code = "patient_not_archived"
    public_message = "The patient is not archived."


class PatientVersionConflictError(PatientError):
    """Raised when a mutation uses a stale patient version."""

    code = "patient_version_conflict"
    public_message = "The patient version is no longer current."


class PatientExternalReferenceConflictError(PatientError):
    """Raised when a tenant-local external reference is already in use."""

    code = "patient_external_reference_conflict"
    public_message = "A patient with the same external reference already exists."


class PatientInvalidDateOfBirthError(PatientError):
    """Raised when a patient's date of birth is in the future."""

    code = "patient_invalid_date_of_birth"
    public_message = "The patient date of birth is invalid."


class PatientInvalidUpdateError(PatientError):
    """Raised when a patient patch cannot produce a valid mutation."""

    code = "patient_invalid_update"
    public_message = "The patient update is invalid."


class PatientInvalidCursorError(PatientError):
    """Raised when a patient pagination cursor is malformed."""

    code = "patient_invalid_cursor"
    public_message = "The patient cursor is invalid."


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
