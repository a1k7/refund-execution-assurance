"""Concurrency tests. Barrier-based. No sleeps."""
from __future__ import annotations

import threading

from app.domain.enums import OperationState
from app.persistence.repositories import ReleaseRepository


def test_two_threads_consume_same_release_only_one_wins(world) -> None:
    """Direct test of the atomic primitive under a start barrier."""
    bo = world.create_operation()
    signed = world.issue_release(bo)
    repo = ReleaseRepository(world.db)

    barrier = threading.Barrier(2)
    results: list[tuple[str, bool]] = []
    lock = threading.Lock()

    def worker(attempt_id: str) -> None:
        barrier.wait(timeout=5)
        won = repo.try_consume(signed.payload.release_id, attempt_id, world.clock.now())
        with lock:
            results.append((attempt_id, won))

    threads = [threading.Thread(target=worker, args=(f"att_{i}",)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 2
    winners = [r for r in results if r[1]]
    assert len(winners) == 1
    stored = repo.get(signed.payload.release_id)
    assert stored is not None
    assert stored["consumed_by_attempt_id"] == winners[0][0]


def test_two_concurrent_dispatches_yield_one_receiver_refund(world) -> None:
    """End-to-end: two full dispatches racing -> exactly one receiver refund."""
    bo = world.create_operation()
    signed = world.issue_release(bo)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    results: list = []
    lock = threading.Lock()

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            r = world.coordinator.dispatch(
                business_operation_id=bo,
                release=signed,
                idempotency_key=f"idem_{idx}",
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
    assert len(results) == 2

    # Ground truth: exactly one refund at the receiver.
    assert world.oracle.refund_count() == 1, world.oracle.refund_states()

    # At most one attempt ended in DISPATCH_ACCEPTED / IN_FLIGHT.
    attempts = world.attempts.list_for_operation(bo)
    accepted = [
        a for a in attempts
        if a.state in (OperationState.DISPATCH_ACCEPTED, OperationState.IN_FLIGHT)
    ]
    assert len(accepted) <= 1, [a.state.value for a in attempts]