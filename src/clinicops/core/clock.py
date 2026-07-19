from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Time source required by time-sensitive application services."""

    def now(self) -> datetime:
        """Return the current timezone-aware datetime."""
        ...


class SystemClock:
    """UTC system clock used by production application services."""

    def now(self) -> datetime:
        """Return the current UTC datetime."""

        return datetime.now(UTC)
