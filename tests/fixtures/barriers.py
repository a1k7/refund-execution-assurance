"""Deterministic synchronization primitives for concurrency tests.

Why this exists: the concurrency tests must coordinate two worker threads at
the exact same point without using ``time.sleep``. ``threading.Barrier`` is the
right primitive; this module just wraps it with a stable name and type.
"""
from __future__ import annotations

import threading


def make_barrier(parties: int) -> threading.Barrier:
    return threading.Barrier(parties)


__all__ = ["make_barrier"]