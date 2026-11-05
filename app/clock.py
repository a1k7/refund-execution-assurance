"""Injected clock. Production uses system UTC; tests use a controllable fake."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def advance(self, seconds: float) -> None: ...


@dataclass
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def advance(self, seconds: float) -> None:  # pragma: no cover - noop in prod
        # Real clocks cannot be advanced. Kept for interface parity.
        _ = seconds


@dataclass
class FakeClock:
    """Deterministic clock for tests. All times are UTC-aware."""
    _t: datetime = field(default_factory=lambda: datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    def now(self) -> datetime:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t = self._t + timedelta(seconds=seconds)

    def set(self, t: datetime) -> None:
        if t.tzinfo is None:
            raise ValueError("FakeClock requires tz-aware datetimes")
        self._t = t