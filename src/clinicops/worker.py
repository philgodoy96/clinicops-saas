import logging
import signal
from collections.abc import Callable
from datetime import timedelta
from threading import Event
from types import FrameType

from clinicops.billing.jobs.process_billing_webhook_event import (
    ProcessBillingWebhookEventJobHandler,
    SqlAlchemyBillingWebhookEventProcessor,
)
from clinicops.core.config import Settings, get_settings
from clinicops.db.session import get_session_factory
from clinicops.jobs.retry import BackgroundJobRetryPolicy
from clinicops.jobs.runtime.identity import resolve_worker_id
from clinicops.jobs.runtime.registry import JobHandlerRegistry
from clinicops.jobs.runtime.worker import (
    BackgroundWorker,
    SessionFactory,
    SqlAlchemyBackgroundJobRuntimeStore,
)

logger = logging.getLogger(__name__)

type SignalHandler = Callable[
    [int, FrameType | None],
    None,
]
type SignalRegistrar = Callable[
    [int, SignalHandler],
    object,
]


def configure_worker_logging() -> None:
    """Configure process-level worker logging."""

    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s %(levelname)s %(name)s %(message)s"),
    )


def build_job_handler_registry(
    session_factory: SessionFactory,
) -> JobHandlerRegistry:
    """Build the explicit worker handler registry."""

    billing_webhook_processor = SqlAlchemyBillingWebhookEventProcessor(
        session_factory=session_factory,
    )

    billing_webhook_handler = ProcessBillingWebhookEventJobHandler(billing_webhook_processor)

    return JobHandlerRegistry([billing_webhook_handler])


def build_worker(
    settings: Settings,
    *,
    session_factory: SessionFactory | None = None,
) -> BackgroundWorker:
    """Compose the configured background worker."""

    configured_session_factory = (
        session_factory if session_factory is not None else get_session_factory()
    )

    retry_policy = BackgroundJobRetryPolicy(
        base_delay=timedelta(seconds=(settings.worker_retry_base_delay_seconds)),
        maximum_delay=timedelta(seconds=(settings.worker_retry_maximum_delay_seconds)),
    )

    runtime_store = SqlAlchemyBackgroundJobRuntimeStore(
        session_factory=(configured_session_factory),
        retry_policy=retry_policy,
    )

    registry = build_job_handler_registry(configured_session_factory)

    worker_id = resolve_worker_id(settings.worker_id)

    return BackgroundWorker(
        store=runtime_store,
        registry=registry,
        worker_id=worker_id,
        poll_interval=timedelta(seconds=(settings.worker_poll_interval_seconds)),
        lease_duration=timedelta(seconds=settings.worker_lease_seconds),
        stale_recovery_interval=timedelta(
            seconds=(settings.worker_stale_recovery_interval_seconds)
        ),
        stale_recovery_batch_size=(settings.worker_stale_recovery_batch_size),
    )


def install_shutdown_signal_handlers(
    stop_event: Event,
    *,
    worker_id: str,
    signal_registrar: SignalRegistrar | None = None,
) -> None:
    """Stop future claims after a process signal."""

    registrar = signal_registrar if signal_registrar is not None else _register_signal_handler

    def request_shutdown(
        signal_number: int,
        frame: FrameType | None,
    ) -> None:
        del frame

        logger.info(
            "background_worker_shutdown_requested",
            extra={
                "worker_id": worker_id,
                "signal_number": signal_number,
            },
        )

        stop_event.set()

    registrar(
        signal.SIGTERM,
        request_shutdown,
    )
    registrar(
        signal.SIGINT,
        request_shutdown,
    )


def _register_signal_handler(
    signal_number: int,
    handler: SignalHandler,
) -> object:
    return signal.signal(
        signal_number,
        handler,
    )


def main() -> int:
    """Run the configured ClinicOps worker process."""

    configure_worker_logging()
    stop_event = Event()

    try:
        settings = get_settings()
        worker = build_worker(settings)

        install_shutdown_signal_handlers(
            stop_event,
            worker_id=worker.worker_id,
        )

        logger.info(
            "background_worker_process_ready",
            extra={
                "worker_id": worker.worker_id,
            },
        )

        worker.run_forever(stop_event)
    except KeyboardInterrupt:
        stop_event.set()

        logger.info("background_worker_keyboard_interrupt")

        return 0
    except Exception:
        logger.exception("background_worker_process_failed")

        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SignalHandler",
    "SignalRegistrar",
    "build_job_handler_registry",
    "build_worker",
    "configure_worker_logging",
    "install_shutdown_signal_handlers",
    "main",
]
