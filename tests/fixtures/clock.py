"""Re-export the deterministic clock used by tests.

The implementation lives in ``app.clock`` so production code and tests share the
exact same class. Do not fork it.
"""
from __future__ import annotations

from app.clock import Clock, FakeClock, SystemClock

__all__ = ["Clock", "FakeClock", "SystemClock"]