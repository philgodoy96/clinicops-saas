import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinicops.api.errors import (
    PROBLEM_MEDIA_TYPE,
    register_exception_handlers,
)
from clinicops.api.middleware.request_context import (
    RequestContextMiddleware,
)
from clinicops.patients.exceptions import (
    PatientAlreadyArchivedError,
    PatientExternalReferenceConflictError,
    PatientInvalidCursorError,
    PatientInvalidDateOfBirthError,
    PatientInvalidUpdateError,
    PatientNotArchivedError,
    PatientNotFoundError,
    PatientVersionConflictError,
)


def _error_app(
    exception: Exception,
) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)

    @application.get("/failure")
    def raise_failure() -> None:
        raise exception

    return application


@pytest.mark.parametrize(
    ("exception", "expected_status", "expected_code"),
    [
        (
            PatientInvalidCursorError(),
            400,
            "patient_invalid_cursor",
        ),
        (
            PatientInvalidDateOfBirthError(),
            400,
            "patient_invalid_date_of_birth",
        ),
        (
            PatientInvalidUpdateError(),
            400,
            "patient_invalid_update",
        ),
        (
            PatientNotFoundError(),
            404,
            "patient_not_found",
        ),
        (
            PatientVersionConflictError(),
            409,
            "patient_version_conflict",
        ),
        (
            PatientAlreadyArchivedError(),
            409,
            "patient_already_archived",
        ),
        (
            PatientNotArchivedError(),
            409,
            "patient_not_archived",
        ),
        (
            PatientExternalReferenceConflictError(),
            409,
            "patient_external_reference_conflict",
        ),
    ],
)
def test_patient_error_uses_problem_details(
    exception: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    with TestClient(
        _error_app(exception),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/failure")

    payload = response.json()

    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert payload["status"] == expected_status
    assert payload["code"] == expected_code
    assert payload["request_id"]
    assert payload["correlation_id"]
    assert "jordan@example.com" not in response.text
    assert "+1-202-555-0184" not in response.text
