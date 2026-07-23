"""End-to-end billing webhook ingestion through background worker processing."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from clinicops.api.v1.billing.webhooks import get_billing_webhook_clock
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    BillingProvider,
    BillingWebhookEventStatus,
    SubscriptionStatus,
)
from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
    BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION,
)
from clinicops.billing.jobs.process_billing_webhook_event import (
    ProcessBillingWebhookEventJobHandler,
)
from clinicops.billing.models import (
    BillingCustomer,
    BillingWebhookEvent,
    ProviderOperation,
    Subscription,
)
from clinicops.billing.providers.fake import FakePaymentProvider
from clinicops.billing.webhooks.process import (
    ProcessBillingWebhookEventCommand,
    ProcessBillingWebhookEventService,
)
from clinicops.billing.webhooks.signatures import sign_billing_webhook_payload
from clinicops.core.config import Environment, Settings
from clinicops.db.session import get_engine
from clinicops.jobs.contracts import EnqueueBackgroundJobCommand
from clinicops.jobs.enums import BackgroundJobStatus
from clinicops.jobs.models import BackgroundJob
from clinicops.jobs.repositories.background_job_repository import (
    BackgroundJobRepository,
)
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.runtime.registry import JobHandlerRegistry
from clinicops.jobs.runtime.worker import (
    BackgroundWorker,
    SqlAlchemyBackgroundJobRuntimeStore,
    WorkerIterationOutcome,
)
from clinicops.jobs.services.enqueue_background_job import (
    EnqueueBackgroundJobService,
)
from clinicops.main import create_app
from clinicops.tenancy.models import Tenant
from tests.conftest import IsolatedSettings

SECRET = "integration-billing-webhook-worker-secret"
_BILLING_WEBHOOK_PROCESS_IDEMPOTENCY_PREFIX = "billing-webhook-process"
_TEST_JOB_PRIORITY = 2_147_483_647

PERIOD_START = datetime(2026, 7, 22, 12, tzinfo=UTC)
PERIOD_END = datetime(2026, 8, 22, 12, tzinfo=UTC)
NEXT_PERIOD_END = datetime(2026, 9, 22, 12, tzinfo=UTC)
SIGNATURE_NOW = PERIOD_END
SIGNATURE_TIMESTAMP = int(SIGNATURE_NOW.timestamp())


class FixedClock:
    def now(self) -> datetime:
        return SIGNATURE_NOW


@dataclass(frozen=True, slots=True)
class BillingPrerequisite:
    tenant_id: UUID
    billing_customer_id: UUID
    subscription_id: UUID
    provider_customer_id: str
    provider_subscription_id: str


@dataclass
class ClaimVisibilityProbe:
    job_id: UUID
    engine: Engine
    observed_processing_claim: bool = False

    def __call__(self, webhook_event_id: UUID) -> None:
        with Session(self.engine) as session:
            try:
                claimed_job = session.get(BackgroundJob, self.job_id)
                assert claimed_job is not None
                assert claimed_job.status is BackgroundJobStatus.PROCESSING
                assert claimed_job.worker_id is not None
                assert claimed_job.claim_token is not None
                self.observed_processing_claim = True

                ProcessBillingWebhookEventService().execute(
                    session,
                    ProcessBillingWebhookEventCommand(
                        webhook_event_id=webhook_event_id,
                    ),
                )
                session.commit()
            except Exception:
                session.rollback()
                raise


@dataclass
class TrackedResources:
    tenant_id: UUID | None = None
    provider_event_ids: list[str] = field(default_factory=list)
    job_ids: list[UUID] = field(default_factory=list)
    job_idempotency_keys: list[str] = field(default_factory=list)


def _settings() -> Settings:
    return IsolatedSettings(
        environment=Environment.TEST,
        billing_webhook_secret=SecretStr(SECRET),
    )


@contextmanager
def _client() -> Iterator[TestClient]:
    application = create_app(_settings())
    application.state.payment_provider = FakePaymentProvider()
    application.dependency_overrides[get_billing_webhook_clock] = lambda: FixedClock()

    try:
        with TestClient(application) as client:
            yield client
    finally:
        application.dependency_overrides.clear()


def _signature(raw_body: bytes) -> str:
    return sign_billing_webhook_payload(
        raw_body=raw_body,
        secret=SECRET,
        timestamp=SIGNATURE_TIMESTAMP,
    )


def _renewal_raw_body(
    *,
    provider_event_id: str,
    provider_subscription_id: str,
    provider_state_version: int = 5,
    price_code: str = "professional_monthly",
) -> bytes:
    return json.dumps(
        {
            "id": provider_event_id,
            "type": "subscription.renewed",
            "created_at": PERIOD_END.isoformat(),
            "data": {
                "provider_subscription_id": provider_subscription_id,
                "provider_state_version": provider_state_version,
                "price_code": price_code,
                "status": "active",
                "current_period_start": PERIOD_END.isoformat(),
                "current_period_end": NEXT_PERIOD_END.isoformat(),
                "canceled_at": None,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _persist_billing_prerequisites() -> BillingPrerequisite:
    tenant_id = uuid4()
    billing_customer_id = uuid4()
    subscription_id = uuid4()
    provider_customer_id = f"fake_customer_{uuid4().hex}"
    provider_subscription_id = f"fake_sub_{uuid4().hex}"

    with Session(get_engine()) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"Webhook Worker Clinic {tenant_id.hex}",
            )
        )
        session.add(
            BillingCustomer(
                id=billing_customer_id,
                tenant_id=tenant_id,
                provider=BillingProvider.FAKE,
                provider_customer_id=provider_customer_id,
            )
        )
        session.add(
            Subscription(
                id=subscription_id,
                tenant_id=tenant_id,
                billing_customer_id=billing_customer_id,
                provider=BillingProvider.FAKE,
                provider_subscription_id=provider_subscription_id,
                price_code="starter_monthly",
                plan=BillingPlan.STARTER,
                billing_interval=BillingInterval.MONTHLY,
                currency="USD",
                unit_amount=4900,
                pending_price_code="professional_monthly",
                status=SubscriptionStatus.ACTIVE,
                cancel_at_period_end=False,
                cancellation_requested_at=None,
                current_period_start=PERIOD_START,
                current_period_end=PERIOD_END,
                provider_state_version=4,
                last_provider_event_at=None,
                canceled_at=None,
            )
        )
        session.commit()

    return BillingPrerequisite(
        tenant_id=tenant_id,
        billing_customer_id=billing_customer_id,
        subscription_id=subscription_id,
        provider_customer_id=provider_customer_id,
        provider_subscription_id=provider_subscription_id,
    )


def _post_signed_webhook(
    client: TestClient,
    *,
    raw_body: bytes,
    correlation_id: str | None = None,
) -> Response:
    headers: dict[str, str] = {
        "X-Billing-Signature": _signature(raw_body),
    }
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id

    return client.post(
        "/api/v1/billing/webhooks/fake",
        content=raw_body,
        headers=headers,
    )


def _load_event_by_provider_event_id(
    provider_event_id: str,
) -> BillingWebhookEvent:
    with Session(get_engine()) as session:
        event = session.scalar(
            select(BillingWebhookEvent).where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id == provider_event_id,
            )
        )
        assert event is not None
        session.expunge(event)
        return event


def _load_job_by_idempotency_key(
    idempotency_key: str,
) -> BackgroundJob:
    with Session(get_engine()) as session:
        job = session.scalar(
            select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key)
        )
        assert job is not None
        session.expunge(job)
        return job


def _event_count(provider_event_id: str) -> int:
    with Session(get_engine()) as session:
        count = session.scalar(
            select(func.count())
            .select_from(BillingWebhookEvent)
            .where(
                BillingWebhookEvent.provider == BillingProvider.FAKE,
                BillingWebhookEvent.provider_event_id == provider_event_id,
            )
        )
        return count or 0


def _job_count_for_idempotency_key(idempotency_key: str) -> int:
    with Session(get_engine()) as session:
        count = session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.idempotency_key == idempotency_key)
        )
        return count or 0


def _job_count_for_webhook_event_id(webhook_event_id: UUID) -> int:
    with Session(get_engine()) as session:
        count = session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.job_type == BILLING_WEBHOOK_PROCESS_JOB_TYPE,
                BackgroundJob.payload["webhook_event_id"].as_string() == str(webhook_event_id),
            )
        )
        return count or 0


def _subscription_count(tenant_id: UUID) -> int:
    with Session(get_engine()) as session:
        count = session.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.tenant_id == tenant_id)
        )
        return count or 0


def _customer_count(tenant_id: UUID) -> int:
    with Session(get_engine()) as session:
        count = session.scalar(
            select(func.count())
            .select_from(BillingCustomer)
            .where(BillingCustomer.tenant_id == tenant_id)
        )
        return count or 0


def _provider_operation_count(tenant_id: UUID) -> int:
    with Session(get_engine()) as session:
        count = session.scalar(
            select(func.count())
            .select_from(ProviderOperation)
            .where(ProviderOperation.tenant_id == tenant_id)
        )
        return count or 0


def _load_subscription(subscription_id: UUID) -> Subscription:
    with Session(get_engine()) as session:
        subscription = session.get(Subscription, subscription_id)
        assert subscription is not None
        session.expunge(subscription)
        return subscription


def _load_job(job_id: UUID) -> BackgroundJob:
    with Session(get_engine()) as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        session.expunge(job)
        return job


def _load_event(webhook_event_id: UUID) -> BillingWebhookEvent:
    with Session(get_engine()) as session:
        event = session.get(BillingWebhookEvent, webhook_event_id)
        assert event is not None
        session.expunge(event)
        return event


def _prioritize_job(job_id: UUID) -> None:
    with Session(get_engine()) as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        job.priority = _TEST_JOB_PRIORITY
        job.available_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()


def _retry_policy() -> BackgroundJobRetryPolicy:
    return BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=60),
        maximum_delay=timedelta(hours=1),
        random_value_provider=lambda: 0.0,
    )


def _build_worker(
    *,
    engine: Engine,
    process_webhook_event: ClaimVisibilityProbe,
    worker_id: str,
) -> BackgroundWorker:
    handler = ProcessBillingWebhookEventJobHandler(process_webhook_event)
    return BackgroundWorker(
        store=SqlAlchemyBackgroundJobRuntimeStore(
            session_factory=lambda: Session(engine),
            retry_policy=_retry_policy(),
        ),
        registry=JobHandlerRegistry([handler]),
        worker_id=worker_id,
        poll_interval=timedelta(seconds=1),
        lease_duration=timedelta(minutes=5),
        stale_recovery_interval=timedelta(minutes=1),
        stale_recovery_batch_size=50,
    )


def _enqueue_replay_job(
    *,
    webhook_event_id: UUID,
    idempotency_key: str,
    correlation_id: str,
) -> UUID:
    with Session(get_engine()) as session:
        result = EnqueueBackgroundJobService(BackgroundJobRepository(session)).execute(
            EnqueueBackgroundJobCommand(
                job_type=BILLING_WEBHOOK_PROCESS_JOB_TYPE,
                payload_version=BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION,
                payload={"webhook_event_id": str(webhook_event_id)},
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                priority=_TEST_JOB_PRIORITY,
                available_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        session.commit()
        return result.job_id


def _cleanup(resources: TrackedResources) -> None:
    with Session(get_engine()) as session:
        if resources.job_ids:
            session.execute(delete(BackgroundJob).where(BackgroundJob.id.in_(resources.job_ids)))

        if resources.job_idempotency_keys:
            session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.idempotency_key.in_(resources.job_idempotency_keys)
                )
            )

        if resources.provider_event_ids:
            session.execute(
                delete(BillingWebhookEvent).where(
                    BillingWebhookEvent.provider == BillingProvider.FAKE,
                    BillingWebhookEvent.provider_event_id.in_(resources.provider_event_ids),
                )
            )

        if resources.tenant_id is not None:
            tenant_id = resources.tenant_id
            session.execute(
                delete(ProviderOperation).where(ProviderOperation.tenant_id == tenant_id)
            )
            session.execute(delete(Subscription).where(Subscription.tenant_id == tenant_id))
            session.execute(delete(BillingCustomer).where(BillingCustomer.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.id == tenant_id))

        session.commit()


def test_authenticated_webhook_is_processed_by_background_worker() -> None:
    prerequisites = _persist_billing_prerequisites()
    provider_event_id = f"evt_{uuid4().hex}"
    correlation_id = str(uuid4())
    raw_body = _renewal_raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=prerequisites.provider_subscription_id,
    )
    resources = TrackedResources(
        tenant_id=prerequisites.tenant_id,
        provider_event_ids=[provider_event_id],
    )
    engine = get_engine()

    try:
        with _client() as client:
            response = _post_signed_webhook(
                client,
                raw_body=raw_body,
                correlation_id=correlation_id,
            )

        assert response.status_code == 202
        assert response.json() == {"received": True}

        event = _load_event_by_provider_event_id(provider_event_id)
        idempotency_key = f"{_BILLING_WEBHOOK_PROCESS_IDEMPOTENCY_PREFIX}:{event.id}"
        resources.job_idempotency_keys.append(idempotency_key)
        job = _load_job_by_idempotency_key(idempotency_key)
        resources.job_ids.append(job.id)

        assert event.status is BillingWebhookEventStatus.RECEIVED
        assert event.processing_attempt_count == 0
        assert event.processed_at is None
        assert event.failure_code is None
        assert event.failure_message is None
        assert event.correlation_id == correlation_id

        assert job.status is BackgroundJobStatus.QUEUED
        assert job.job_type == BILLING_WEBHOOK_PROCESS_JOB_TYPE
        assert job.payload_version == BILLING_WEBHOOK_PROCESS_PAYLOAD_VERSION
        assert job.payload == {"webhook_event_id": str(event.id)}
        assert job.correlation_id == correlation_id
        assert job.processing_attempt_count == 0
        assert job.completed_at is None
        assert job.worker_id is None
        assert job.claim_token is None

        _prioritize_job(job.id)

        probe = ClaimVisibilityProbe(job_id=job.id, engine=engine)
        worker = _build_worker(
            engine=engine,
            process_webhook_event=probe,
            worker_id=f"billing-webhook-worker-{uuid4().hex[:12]}",
        )
        result = worker.run_once()

        assert result.outcome is WorkerIterationOutcome.SUCCEEDED
        assert result.job_id == job.id
        assert probe.observed_processing_claim is True

        completed_job = _load_job(job.id)
        completed_event = _load_event(event.id)
        subscription = _load_subscription(prerequisites.subscription_id)

        assert completed_job.status is BackgroundJobStatus.SUCCEEDED
        assert completed_job.processing_attempt_count == 1
        assert completed_job.completed_at is not None
        assert completed_job.worker_id is None
        assert completed_job.claim_token is None
        assert completed_job.claimed_at is None
        assert completed_job.lease_expires_at is None
        assert completed_job.last_error_code is None
        assert completed_job.correlation_id == correlation_id

        assert completed_event.status is BillingWebhookEventStatus.PROCESSED
        assert completed_event.processed_at is not None
        assert completed_event.processing_attempt_count == 1
        assert completed_event.failure_code is None
        assert completed_event.failure_message is None
        assert completed_event.correlation_id == correlation_id

        assert subscription.price_code == "professional_monthly"
        assert subscription.plan is BillingPlan.PROFESSIONAL
        assert subscription.billing_interval is BillingInterval.MONTHLY
        assert subscription.unit_amount == 9900
        assert subscription.pending_price_code is None
        assert subscription.status is SubscriptionStatus.ACTIVE
        assert subscription.current_period_start == PERIOD_END
        assert subscription.current_period_end == NEXT_PERIOD_END
        assert subscription.provider_state_version == 5
        assert subscription.last_provider_event_at == PERIOD_END
    finally:
        _cleanup(resources)


def test_duplicate_webhook_delivery_reuses_event_and_processing_job() -> None:
    prerequisites = _persist_billing_prerequisites()
    provider_event_id = f"evt_{uuid4().hex}"
    correlation_id = str(uuid4())
    raw_body = _renewal_raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=prerequisites.provider_subscription_id,
    )
    resources = TrackedResources(
        tenant_id=prerequisites.tenant_id,
        provider_event_ids=[provider_event_id],
    )
    engine = get_engine()

    try:
        with _client() as client:
            first = _post_signed_webhook(
                client,
                raw_body=raw_body,
                correlation_id=correlation_id,
            )
            duplicate = _post_signed_webhook(
                client,
                raw_body=raw_body,
                correlation_id=str(uuid4()),
            )

        assert first.status_code == 202
        assert first.json() == {"received": True}
        assert duplicate.status_code == 202
        assert duplicate.json() == {"received": True}

        assert _event_count(provider_event_id) == 1

        event = _load_event_by_provider_event_id(provider_event_id)
        idempotency_key = f"{_BILLING_WEBHOOK_PROCESS_IDEMPOTENCY_PREFIX}:{event.id}"
        resources.job_idempotency_keys.append(idempotency_key)

        assert _job_count_for_idempotency_key(idempotency_key) == 1

        job = _load_job_by_idempotency_key(idempotency_key)
        resources.job_ids.append(job.id)

        assert job.payload == {"webhook_event_id": str(event.id)}
        assert _job_count_for_webhook_event_id(event.id) == 1

        _prioritize_job(job.id)

        probe = ClaimVisibilityProbe(job_id=job.id, engine=engine)
        worker = _build_worker(
            engine=engine,
            process_webhook_event=probe,
            worker_id=f"billing-webhook-worker-{uuid4().hex[:12]}",
        )
        first_result = worker.run_once()

        assert first_result.outcome is WorkerIterationOutcome.SUCCEEDED
        assert first_result.job_id == job.id

        completed_job = _load_job(job.id)
        completed_event = _load_event(event.id)

        assert completed_job.status is BackgroundJobStatus.SUCCEEDED
        assert completed_event.status is BillingWebhookEventStatus.PROCESSED

        second_result = worker.run_once()

        assert second_result.outcome is WorkerIterationOutcome.IDLE
        assert second_result.job_id is None

        assert _event_count(provider_event_id) == 1
        assert _job_count_for_idempotency_key(idempotency_key) == 1
        assert _job_count_for_webhook_event_id(event.id) == 1
        assert _subscription_count(prerequisites.tenant_id) == 1
    finally:
        _cleanup(resources)


def test_replayed_billing_event_job_remains_domain_safe() -> None:
    prerequisites = _persist_billing_prerequisites()
    provider_event_id = f"evt_{uuid4().hex}"
    correlation_id = str(uuid4())
    raw_body = _renewal_raw_body(
        provider_event_id=provider_event_id,
        provider_subscription_id=prerequisites.provider_subscription_id,
    )
    resources = TrackedResources(
        tenant_id=prerequisites.tenant_id,
        provider_event_ids=[provider_event_id],
    )
    engine = get_engine()

    try:
        with _client() as client:
            response = _post_signed_webhook(
                client,
                raw_body=raw_body,
                correlation_id=correlation_id,
            )

        assert response.status_code == 202

        event = _load_event_by_provider_event_id(provider_event_id)
        idempotency_key = f"{_BILLING_WEBHOOK_PROCESS_IDEMPOTENCY_PREFIX}:{event.id}"
        resources.job_idempotency_keys.append(idempotency_key)
        job = _load_job_by_idempotency_key(idempotency_key)
        resources.job_ids.append(job.id)

        _prioritize_job(job.id)

        probe = ClaimVisibilityProbe(job_id=job.id, engine=engine)
        worker = _build_worker(
            engine=engine,
            process_webhook_event=probe,
            worker_id=f"billing-webhook-worker-{uuid4().hex[:12]}",
        )
        first_result = worker.run_once()

        assert first_result.outcome is WorkerIterationOutcome.SUCCEEDED
        assert first_result.job_id == job.id

        subscription_after_apply = _load_subscription(prerequisites.subscription_id)
        event_after_apply = _load_event(event.id)

        assert event_after_apply.status is BillingWebhookEventStatus.PROCESSED
        assert subscription_after_apply.provider_state_version == 5
        assert subscription_after_apply.price_code == "professional_monthly"
        assert subscription_after_apply.current_period_end == NEXT_PERIOD_END

        recorded_provider_state_version = subscription_after_apply.provider_state_version
        recorded_price_code = subscription_after_apply.price_code
        recorded_period_start = subscription_after_apply.current_period_start
        recorded_period_end = subscription_after_apply.current_period_end
        recorded_last_provider_event_at = subscription_after_apply.last_provider_event_at
        recorded_subscription_count = _subscription_count(prerequisites.tenant_id)
        recorded_customer_count = _customer_count(prerequisites.tenant_id)
        recorded_provider_operation_count = _provider_operation_count(prerequisites.tenant_id)

        replay_idempotency_key = f"billing-webhook-process-replay-test:{uuid4()}"
        resources.job_idempotency_keys.append(replay_idempotency_key)
        replay_job_id = _enqueue_replay_job(
            webhook_event_id=event.id,
            idempotency_key=replay_idempotency_key,
            correlation_id=f"replay-{uuid4()}",
        )
        resources.job_ids.append(replay_job_id)

        replay_probe = ClaimVisibilityProbe(job_id=replay_job_id, engine=engine)
        replay_worker = _build_worker(
            engine=engine,
            process_webhook_event=replay_probe,
            worker_id=f"billing-webhook-worker-{uuid4().hex[:12]}",
        )
        replay_result = replay_worker.run_once()

        assert replay_result.outcome is WorkerIterationOutcome.SUCCEEDED
        assert replay_result.job_id == replay_job_id
        assert replay_probe.observed_processing_claim is True

        replayed_job = _load_job(replay_job_id)
        replayed_event = _load_event(event.id)
        subscription_after_replay = _load_subscription(prerequisites.subscription_id)

        assert replayed_job.status is BackgroundJobStatus.SUCCEEDED
        assert replayed_job.processing_attempt_count == 1
        assert replayed_job.completed_at is not None
        assert replayed_job.worker_id is None
        assert replayed_job.claim_token is None

        assert replayed_event.status is BillingWebhookEventStatus.PROCESSED
        assert replayed_event.processed_at == event_after_apply.processed_at
        assert replayed_event.processing_attempt_count == event_after_apply.processing_attempt_count
        assert replayed_event.failure_code is None
        assert replayed_event.failure_message is None

        assert subscription_after_replay.provider_state_version == recorded_provider_state_version
        assert subscription_after_replay.price_code == recorded_price_code
        assert subscription_after_replay.current_period_start == recorded_period_start
        assert subscription_after_replay.current_period_end == recorded_period_end
        assert subscription_after_replay.last_provider_event_at == recorded_last_provider_event_at
        assert subscription_after_replay.pending_price_code is None
        assert subscription_after_replay.status is SubscriptionStatus.ACTIVE

        assert _subscription_count(prerequisites.tenant_id) == recorded_subscription_count
        assert _customer_count(prerequisites.tenant_id) == recorded_customer_count
        assert (
            _provider_operation_count(prerequisites.tenant_id) == recorded_provider_operation_count
        )
        assert _event_count(provider_event_id) == 1
        assert _job_count_for_webhook_event_id(event.id) == 2
    finally:
        _cleanup(resources)
