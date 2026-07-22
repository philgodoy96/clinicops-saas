from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from clinicops.api.dependencies import (
    get_database_session,
)
from clinicops.api.errors import register_exception_handlers
from clinicops.api.v1.billing.webhooks import (
    get_billing_webhook_clock,
    get_ingest_billing_webhook_service,
    router,
)
from clinicops.billing.enums import (
    BillingProvider,
    BillingWebhookEventStatus,
)
from clinicops.billing.exceptions import (
    BillingWebhookEventConflictError,
    BillingWebhookPayloadInvalidError,
)
from clinicops.billing.webhooks.ingest import (
    IngestBillingWebhookCommand,
    IngestedBillingWebhook,
)
from clinicops.billing.webhooks.signatures import (
    sign_billing_webhook_payload,
)
from clinicops.core.config import Settings

SECRET = "test-billing-webhook-secret-with-32-bytes"
NOW = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
TIMESTAMP = int(NOW.timestamp())
RAW_BODY = (
    b'{"id":"evt_renewed_01",'
    b'"type":"subscription.renewed",'
    b'"created_at":"2026-08-22T12:00:00Z",'
    b'"data":{'
    b'"provider_subscription_id":"fake_sub_unknown",'
    b'"provider_state_version":4,'
    b'"price_code":"professional_monthly",'
    b'"status":"active",'
    b'"current_period_start":"2026-08-22T12:00:00Z",'
    b'"current_period_end":"2026-09-22T12:00:00Z",'
    b'"canceled_at":null}}'
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class RecordingIngestionService:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        duplicate: bool = False,
    ) -> None:
        self._error = error
        self._duplicate = duplicate
        self.command: IngestBillingWebhookCommand | None = None

    def execute(
        self,
        session: Session,
        command: IngestBillingWebhookCommand,
    ) -> IngestedBillingWebhook:
        self.command = command

        if self._error is not None:
            raise self._error

        return IngestedBillingWebhook(
            webhook_event_id=UUID("2250a0e2-d500-41d8-87c5-b9f48d63b6e8"),
            provider=command.provider,
            provider_event_id="evt_renewed_01",
            status=(BillingWebhookEventStatus.RECEIVED),
            duplicate=self._duplicate,
        )


def _settings(
    settings_factory: Callable[..., Settings],
    *,
    max_payload_bytes: int = 256 * 1024,
    tolerance_seconds: int = 300,
) -> Settings:
    return settings_factory(
        billing_webhook_secret=SECRET,
        billing_webhook_max_payload_bytes=(max_payload_bytes),
        billing_webhook_signature_tolerance_seconds=(tolerance_seconds),
    )


def _signature(
    raw_body: bytes = RAW_BODY,
    *,
    timestamp: int = TIMESTAMP,
) -> str:
    return sign_billing_webhook_payload(
        raw_body=raw_body,
        secret=SECRET,
        timestamp=timestamp,
    )


def _build_client(
    service: RecordingIngestionService,
    session: RecordingSession,
    *,
    settings: Settings,
) -> TestClient:
    application = FastAPI()
    application.state.settings = settings
    register_exception_handlers(application)
    application.include_router(
        router,
        prefix="/api/v1",
    )

    def database_session() -> Iterator[Session]:
        yield cast(Session, session)

    application.dependency_overrides[get_database_session] = database_session
    application.dependency_overrides[get_billing_webhook_clock] = lambda: FixedClock()
    application.dependency_overrides[get_ingest_billing_webhook_service] = lambda: service

    return TestClient(
        application,
        raise_server_exceptions=False,
    )


def test_authenticated_webhook_is_committed_before_acknowledgement(
    settings_factory: Callable[..., Settings],
) -> None:
    service = RecordingIngestionService()
    session = RecordingSession()
    client = _build_client(
        service,
        session,
        settings=_settings(settings_factory),
    )

    response = client.post(
        "/api/v1/billing/webhooks/fake",
        content=RAW_BODY,
        headers={
            "X-Billing-Signature": _signature(),
            "X-Correlation-ID": ("1a414d78-3ddb-4188-93c8-aa2f20f45e75"),
        },
    )

    assert response.status_code == 202
    assert response.json() == {"received": True}
    assert session.commit_count == 1
    assert service.command is not None
    assert service.command.provider is BillingProvider.FAKE
    assert service.command.raw_body == RAW_BODY
    assert service.command.signature_timestamp == TIMESTAMP


def test_webhook_does_not_require_bearer_authentication(
    settings_factory: Callable[..., Settings],
) -> None:
    client = _build_client(
        RecordingIngestionService(),
        RecordingSession(),
        settings=_settings(settings_factory),
    )

    response = client.post(
        "/api/v1/billing/webhooks/fake",
        content=RAW_BODY,
        headers={
            "X-Billing-Signature": _signature(),
        },
    )

    assert response.status_code == 202


def test_duplicate_receipt_is_not_exposed_to_provider(
    settings_factory: Callable[..., Settings],
) -> None:
    client = _build_client(
        RecordingIngestionService(duplicate=True),
        RecordingSession(),
        settings=_settings(settings_factory),
    )

    response = client.post(
        "/api/v1/billing/webhooks/fake",
        content=RAW_BODY,
        headers={
            "X-Billing-Signature": _signature(),
        },
    )

    assert response.status_code == 202
    assert response.json() == {"received": True}
    assert "duplicate" not in response.json()


@pytest.mark.parametrize(
    "signature_header",
    [
        None,
        "t=1,v1=" + ("a" * 64),
    ],
)
def test_invalid_signature_returns_unauthorized_problem(
    signature_header: str | None,
    settings_factory: Callable[..., Settings],
) -> None:
    service = RecordingIngestionService()
    session = RecordingSession()
    client = _build_client(
        service,
        session,
        settings=_settings(settings_factory),
    )
    headers = {}

    if signature_header is not None:
        headers["X-Billing-Signature"] = signature_header

    response = client.post(
        "/api/v1/billing/webhooks/fake",
        content=RAW_BODY,
        headers=headers,
    )

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == ("billing_webhook_authentication_failed")
    assert service.command is None
    assert session.commit_count == 0


def test_stale_signature_returns_unauthorized_problem(
    settings_factory: Callable[..., Settings],
) -> None:
    client = _build_client(
        RecordingIngestionService(),
        RecordingSession(),
        settings=_settings(settings_factory),
    )

    response = client.post(
        "/api/v1/billing/webhooks/fake",
        content=RAW_BODY,
        headers={
            "X-Billing-Signature": _signature(timestamp=TIMESTAMP - 301),
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == ("billing_webhook_authentication_failed")


def test_oversized_payload_returns_payload_too_large_problem(
    settings_factory: Callable[..., Settings],
) -> None:
    raw_body = b"x" * 11
    service = RecordingIngestionService()
    session = RecordingSession()
    client = _build_client(
        service,
        session,
        settings=_settings(
            settings_factory,
            max_payload_bytes=10,
        ),
    )

    response = client.post(
        "/api/v1/billing/webhooks/fake",
        content=raw_body,
        headers={
            "X-Billing-Signature": _signature(raw_body),
        },
    )

    assert response.status_code == 413
    assert response.json()["code"] == ("billing_webhook_payload_too_large")
    assert service.command is None
    assert session.commit_count == 0


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            BillingWebhookPayloadInvalidError(),
            400,
            "billing_webhook_payload_invalid",
        ),
        (
            BillingWebhookEventConflictError(provider_event_id="evt_renewed_01"),
            409,
            "billing_webhook_event_conflict",
        ),
    ],
)
def test_ingestion_failures_return_problem_details_without_commit(
    error: Exception,
    expected_status: int,
    expected_code: str,
    settings_factory: Callable[..., Settings],
) -> None:
    service = RecordingIngestionService(error=error)
    session = RecordingSession()
    client = _build_client(
        service,
        session,
        settings=_settings(settings_factory),
    )

    response = client.post(
        "/api/v1/billing/webhooks/fake",
        content=RAW_BODY,
        headers={
            "X-Billing-Signature": _signature(),
        },
    )

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert session.commit_count == 0


def test_unknown_provider_returns_not_found_problem(
    settings_factory: Callable[..., Settings],
) -> None:
    service = RecordingIngestionService()
    session = RecordingSession()
    client = _build_client(
        service,
        session,
        settings=_settings(settings_factory),
    )

    response = client.post(
        "/api/v1/billing/webhooks/unsupported",
        content=RAW_BODY,
        headers={
            "X-Billing-Signature": _signature(),
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == ("billing_webhook_provider_not_found")
    assert service.command is None
    assert session.commit_count == 0
