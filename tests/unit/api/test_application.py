from clinicops.api.router import API_V1_PREFIX
from clinicops.api.v1.health import get_liveness
from clinicops.main import create_app


def test_application_exposes_expected_metadata() -> None:
    application = create_app()
    openapi_schema = application.openapi()
    settings = application.state.settings

    assert openapi_schema["info"]["title"] == settings.app_name
    assert openapi_schema["info"]["version"] == settings.app_version


def test_application_exposes_versioned_health_contract() -> None:
    application = create_app()
    openapi_schema = application.openapi()
    health_path = f"{API_V1_PREFIX}/health/live"

    assert health_path in openapi_schema["paths"]
    assert "get" in openapi_schema["paths"][health_path]

    operation = openapi_schema["paths"][health_path]["get"]

    assert operation["summary"] == "Check process liveness"
    assert operation["tags"] == ["system"]
    assert operation["responses"]["200"]["description"] == ("Successful Response")


def test_application_does_not_expose_unversioned_health_route() -> None:
    application = create_app()
    openapi_schema = application.openapi()

    assert "/health" not in openapi_schema["paths"]
    assert "/health/live" not in openapi_schema["paths"]


def test_health_contract_returns_ok_status() -> None:
    response = get_liveness()

    assert response.model_dump() == {"status": "ok"}
