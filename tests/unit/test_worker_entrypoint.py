import inspect
import signal
from collections.abc import Callable
from threading import Event
from types import FrameType
from typing import cast

import pytest
from sqlalchemy.orm import Session

import clinicops.worker as worker_module
from clinicops.billing.jobs.constants import (
    BILLING_WEBHOOK_PROCESS_JOB_TYPE,
)
from clinicops.billing.jobs.process_billing_webhook_event import (
    ProcessBillingWebhookEventJobHandler,
)
from clinicops.core.config import Settings
from clinicops.jobs.runtime.worker import BackgroundWorker


def _unused_session_factory() -> Session:
    raise AssertionError("The session factory must not be called while composing the worker.")


def test_registry_registers_billing_webhook_handler() -> None:
    registry = worker_module.build_job_handler_registry(
        _unused_session_factory,
    )

    handler = registry.resolve(BILLING_WEBHOOK_PROCESS_JOB_TYPE)

    assert isinstance(
        handler,
        ProcessBillingWebhookEventJobHandler,
    )
    assert handler.supported_payload_version == 1


def test_build_worker_uses_configured_identity(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        worker_id="worker-entrypoint-test",
    )

    worker = worker_module.build_worker(
        settings,
        session_factory=_unused_session_factory,
    )

    assert worker.worker_id == ("worker-entrypoint-test")


def test_build_worker_generates_identity_when_missing(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        worker_id=None,
    )

    worker = worker_module.build_worker(
        settings,
        session_factory=_unused_session_factory,
    )

    assert worker.worker_id
    assert len(worker.worker_id) <= 255


def test_shutdown_handlers_set_stop_event() -> None:
    stop_event = Event()
    registered_handlers: dict[
        int,
        worker_module.SignalHandler,
    ] = {}

    def register_signal(
        signal_number: int,
        handler: worker_module.SignalHandler,
    ) -> object:
        registered_handlers[signal_number] = handler

        return object()

    worker_module.install_shutdown_signal_handlers(
        stop_event,
        worker_id="worker-1",
        signal_registrar=register_signal,
    )

    assert signal.SIGTERM in registered_handlers
    assert signal.SIGINT in registered_handlers
    assert stop_event.is_set() is False

    registered_handlers[signal.SIGTERM](
        signal.SIGTERM,
        None,
    )

    assert stop_event.is_set() is True


def test_sigint_handler_sets_stop_event() -> None:
    stop_event = Event()
    registered_handlers: dict[
        int,
        worker_module.SignalHandler,
    ] = {}

    def register_signal(
        signal_number: int,
        handler: worker_module.SignalHandler,
    ) -> object:
        registered_handlers[signal_number] = handler

        return object()

    worker_module.install_shutdown_signal_handlers(
        stop_event,
        worker_id="worker-1",
        signal_registrar=register_signal,
    )

    registered_handlers[signal.SIGINT](
        signal.SIGINT,
        cast(FrameType | None, None),
    )

    assert stop_event.is_set() is True


class RecordingProcessWorker:
    def __init__(
        self,
        *,
        worker_id: str = "worker-main-test",
        error: BaseException | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.error = error
        self.run_count = 0
        self.received_stop_event: Event | None = None

    def run_forever(
        self,
        stop_event: Event,
    ) -> None:
        self.run_count += 1
        self.received_stop_event = stop_event

        if self.error is not None:
            raise self.error


def test_main_loads_settings_and_runs_worker(
    monkeypatch: pytest.MonkeyPatch,
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        worker_id="worker-main-test",
    )
    process_worker = RecordingProcessWorker()
    configured_logging: list[bool] = []
    installed_handlers: list[tuple[Event, str]] = []

    monkeypatch.setattr(
        worker_module,
        "configure_worker_logging",
        lambda: configured_logging.append(True),
    )
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        worker_module,
        "build_worker",
        lambda configured_settings: cast(
            BackgroundWorker,
            process_worker,
        ),
    )

    def install_handlers(
        stop_event: Event,
        *,
        worker_id: str,
        signal_registrar: (worker_module.SignalRegistrar | None) = None,
    ) -> None:
        del signal_registrar

        installed_handlers.append((stop_event, worker_id))

    monkeypatch.setattr(
        worker_module,
        "install_shutdown_signal_handlers",
        install_handlers,
    )

    exit_code = worker_module.main()

    assert exit_code == 0
    assert configured_logging == [True]
    assert process_worker.run_count == 1
    assert process_worker.received_stop_event is not None
    assert installed_handlers == [
        (
            process_worker.received_stop_event,
            "worker-main-test",
        )
    ]


def test_main_returns_failure_code_when_worker_crashes(
    monkeypatch: pytest.MonkeyPatch,
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        worker_id="worker-failure-test",
    )
    process_worker = RecordingProcessWorker(
        worker_id="worker-failure-test",
        error=RuntimeError("worker runtime failure"),
    )

    monkeypatch.setattr(
        worker_module,
        "configure_worker_logging",
        lambda: None,
    )
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        worker_module,
        "build_worker",
        lambda configured_settings: cast(
            BackgroundWorker,
            process_worker,
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "install_shutdown_signal_handlers",
        lambda stop_event, worker_id: None,
    )

    exit_code = worker_module.main()

    assert exit_code == 1
    assert process_worker.run_count == 1


def test_main_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        worker_id="worker-interrupt-test",
    )
    process_worker = RecordingProcessWorker(
        worker_id="worker-interrupt-test",
        error=KeyboardInterrupt(),
    )

    monkeypatch.setattr(
        worker_module,
        "configure_worker_logging",
        lambda: None,
    )
    monkeypatch.setattr(
        worker_module,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        worker_module,
        "build_worker",
        lambda configured_settings: cast(
            BackgroundWorker,
            process_worker,
        ),
    )
    monkeypatch.setattr(
        worker_module,
        "install_shutdown_signal_handlers",
        lambda stop_event, worker_id: None,
    )

    exit_code = worker_module.main()

    assert exit_code == 0
    assert process_worker.run_count == 1


def test_worker_module_does_not_compose_fastapi() -> None:
    source = inspect.getsource(worker_module)

    assert "clinicops.main" not in source
    assert "FastAPI" not in source
    assert not hasattr(worker_module, "app")
