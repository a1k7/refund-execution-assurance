"""Scenario 10 — Two workers race to dispatch the same business operation.

The authoritative concurrent version lives in ``tests/test_concurrency.py``.
Here we exercise the end-to-end scenario through ``world.coordinator.dispatch``
with a real ``threading.Barrier``.
"""
from __future__ import annotations

import threading

from app.domain.enums import OperationState


def test_concurrent_dispatch_yields_one_accepted_attempt(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)

    barrier = threading.Barrier(2)
    results: list = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            r = world.coordinator.dispatch(
                business_operation_id=bo, release=signed, idempotency_key=f"idem_{idx}"
            )
            with lock:
                results.append(r)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"unexpected exceptions: {errors}"
    assert len(results) == 2, f"expected 2 results, got {results}"

    # Ground truth at the receiver: exactly one refund exists.
    assert world.oracle.refund_count() == 1, world.oracle.refund_states()

    # DB: at most one attempt ended accepted/in-flight.
    attempts = world.attempts.list_for_operation(bo)
    accepted = [
        a for a in attempts
        if a.state in (OperationState.DISPATCH_ACCEPTED, OperationState.IN_FLIGHT)
    ]
    assert len(accepted) <= 1, [a.state.value for a in attempts]
    # Exactly one attempt was created and one was rejected with a duplicate reason.
    rejected = [r for r in results if "duplicate_guard" in (r.reason or "")]
    assert len(rejected) <= 1