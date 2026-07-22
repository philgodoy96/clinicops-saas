from collections import deque
from enum import StrEnum
from threading import Lock

from clinicops.billing.enums import ProviderOperationType


class FakeProviderOutcome(StrEnum):
    """Controlled outcome for the next fake-provider operation."""

    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_REJECTION = "terminal_rejection"
    AMBIGUOUS_SUCCESS = "ambiguous_success"


class FakeProviderControl:
    """Thread-safe scripting control for fake-provider outcomes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._outcomes: dict[
            ProviderOperationType,
            deque[FakeProviderOutcome],
        ] = {}

    def queue_outcome(
        self,
        *,
        operation_type: ProviderOperationType,
        outcome: FakeProviderOutcome,
    ) -> None:
        """Append an outcome for one future provider operation."""

        with self._lock:
            outcomes = self._outcomes.setdefault(
                operation_type,
                deque(),
            )
            outcomes.append(outcome)

    def consume_next(
        self,
        *,
        operation_type: ProviderOperationType,
    ) -> FakeProviderOutcome:
        """Consume the next scripted outcome or return success."""

        with self._lock:
            outcomes = self._outcomes.get(operation_type)

            if not outcomes:
                return FakeProviderOutcome.SUCCESS

            outcome = outcomes.popleft()

            if not outcomes:
                self._outcomes.pop(operation_type, None)

            return outcome
