from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from random import random

from clinicops.jobs.exceptions import (
    BackgroundJobInvalidConfigurationError,
)

RandomValueProvider = Callable[[], float]


@dataclass(frozen=True, slots=True)
class BackgroundJobRetryPolicy:
    base_delay: timedelta
    maximum_delay: timedelta
    random_value_provider: RandomValueProvider = field(
        default=random,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.base_delay, timedelta):
            raise BackgroundJobInvalidConfigurationError("base_delay must be a timedelta.")

        if not isinstance(self.maximum_delay, timedelta):
            raise BackgroundJobInvalidConfigurationError("maximum_delay must be a timedelta.")

        if self.base_delay <= timedelta(0):
            raise BackgroundJobInvalidConfigurationError("base_delay must be greater than zero.")

        if self.maximum_delay < self.base_delay:
            raise BackgroundJobInvalidConfigurationError(
                "maximum_delay must be greater than or equal to base_delay."
            )

        if not callable(self.random_value_provider):
            raise BackgroundJobInvalidConfigurationError("random_value_provider must be callable.")

    def calculate_delay(
        self,
        *,
        attempt_number: int,
    ) -> timedelta:
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int):
            raise BackgroundJobInvalidConfigurationError("attempt_number must be an integer.")

        if attempt_number < 1:
            raise BackgroundJobInvalidConfigurationError(
                "attempt_number must be greater than or equal to 1."
            )

        capped_delay = self._calculate_capped_delay(attempt_number=attempt_number)
        random_value = self.random_value_provider()

        if (
            isinstance(random_value, bool)
            or not isinstance(random_value, int | float)
            or random_value < 0
            or random_value > 1
        ):
            raise BackgroundJobInvalidConfigurationError(
                "random_value_provider must return a number between 0 and 1."
            )

        half_delay = capped_delay / 2

        return half_delay + (half_delay * float(random_value))

    def _calculate_capped_delay(
        self,
        *,
        attempt_number: int,
    ) -> timedelta:
        delay = self.base_delay
        remaining_doublings = attempt_number - 1

        while remaining_doublings > 0 and delay < self.maximum_delay:
            delay = min(
                delay * 2,
                self.maximum_delay,
            )
            remaining_doublings -= 1

        return delay


__all__ = [
    "BackgroundJobRetryPolicy",
    "RandomValueProvider",
]
