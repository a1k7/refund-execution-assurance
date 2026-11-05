"""Atomic release consumption.

This module is a thin, explicit façade over ``ReleaseRepository.try_consume``.

What it guarantees
------------------
Within this single-process SQLite architecture, at most ONE worker succeeds in
consuming a given release_id, and every other worker that reaches this step
for the same release_id observes a failure. The guarantee is enforced by
SQLite's ``BEGIN IMMEDIATE`` + a conditional UPDATE; no application mutex is
required (and none is used) for correctness.

What it does NOT guarantee
--------------------------
It does not guarantee the external receiver accepted the request. A crash
between local consumption and the external call leaves the release consumed
but the external effect unknown. Receiver-side fencing is the second layer
required for the external side. See ARCHITECTURE.md §"Two-layer defense".
"""
from __future__ import annotations

from datetime import datetime

from ..persistence.repositories import ReleaseRepository


class AtomicReleaseConsumption:
    def __init__(self, releases: ReleaseRepository) -> None:
        self._releases = releases

    def try_consume(
        self,
        release_id: str,
        dispatch_attempt_id: str,
        now: datetime,
    ) -> bool:
        return self._releases.try_consume(release_id, dispatch_attempt_id, now)