"""Receiver fencing primitives.

The receiver keeps its own authoritative ``current_epoch`` per operation.
A dispatch that presents ``release_epoch < current_epoch`` is rejected as
stale; a fresh dispatch at ``release_epoch >= current_epoch`` is accepted
(subject to other receiver behaviour).

Freshness assumption: fencing is effective only when the receiver's epoch
is authoritative and up to date. In this reference implementation the
``RevocationService`` synchronously propagates the new epoch via
``update_fencing_state``. Real receivers must document their propagation
latency and expose it as part of the integration contract.
"""
from __future__ import annotations

from threading import Lock
from typing import Dict


class FencingRegistry:
    """Thread-safe epoch registry per business operation."""

    def __init__(self) -> None:
        self._epochs: Dict[str, int] = {}
        self._lock = Lock()

    def update(self, business_operation_id: str, epoch: int) -> None:
        with self._lock:
            current = self._epochs.get(business_operation_id, 0)
            if epoch > current:
                self._epochs[business_operation_id] = epoch

    def current(self, business_operation_id: str) -> int:
        with self._lock:
            return self._epochs.get(business_operation_id, 0)

    def is_stale(self, business_operation_id: str, release_epoch: int) -> bool:
        return release_epoch < self.current(business_operation_id)

    def reset(self) -> None:
        with self._lock:
            self._epochs.clear()