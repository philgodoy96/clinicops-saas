from fastapi import status
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from clinicops.api.v1 import health as health_module
from clinicops.db.exceptions import DatabaseUnavailableError


def test_liveness_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_readiness_returns_ok_when_database_is_available(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health_module,
        "check_database_connection",
        lambda: None,
    )

    response = client.get("/api/v1/health/ready")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_readiness_returns_service_unavailable_when_database_check_fails(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    def raise_database_error() -> None:
        raise DatabaseUnavailableError("database unavailable")

    monkeypatch.setattr(
        health_module,
        "check_database_connection",
        raise_database_error,
    )

    response = client.get("/api/v1/health/ready")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    assert body["code"] == "database_unavailable"
    assert body["detail"] == "The database is unavailable."
