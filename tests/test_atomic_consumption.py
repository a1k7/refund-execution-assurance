"""Atomic release consumption: single-shot semantics under concurrency."""
from __future__ import annotations

import threading

from app.persistence.repositories import ReleaseRepository


def test_try_consume_succeeds_once(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    repo = ReleaseRepository(world.db)

    first = repo.try_consume(signed.payload.release_id, "att_1", world.clock.now())
    second = repo.try_consume(signed.payload.release_id, "att_2", world.clock.now())

    assert first is True
    assert second is False


def test_try_consume_records_the_winning_attempt(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    repo = ReleaseRepository(world.db)
    assert repo.try_consume(signed.payload.release_id, "att_win", world.clock.now())

    stored = repo.get(signed.payload.release_id)
    assert stored is not None
    assert stored["consumed_by_attempt_id"] == "att_win"
    assert stored["consumed_at"] is not None


def test_try_consume_on_unknown_release_returns_false(world) -> None:
    repo = ReleaseRepository(world.db)
    assert repo.try_consume("rel_does_not_exist", "att_x", world.clock.now()) is False


def test_try_consume_is_atomic_across_threads(world) -> None:
    """Two threads race to consume the same release. Exactly one wins.

    Uses threading.Barrier to synchronize the start (no sleeps anywhere).
    """
    bo = world.create_operation()
    signed = world.issue_release(bo)
    repo = ReleaseRepository(world.db)

    barrier = threading.Barrier(2)
    results: list[tuple[str, bool]] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def worker(attempt_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            won = repo.try_consume(signed.payload.release_id, attempt_id, world.clock.now())
            with result_lock:
                results.append((attempt_id, won))
        except BaseException as exc:  # noqa: BLE001
            with result_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("att_A",)),
        threading.Thread(target=worker, args=("att_B",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"unexpected exceptions: {errors}"
    assert len(results) == 2, f"expected 2 results, got {results}"
    winners = [r for r in results if r[1]]
    losers = [r for r in results if not r[1]]
    assert len(winners) == 1, f"expected exactly one winner, got {winners}"
    assert len(losers) == 1, f"expected exactly one loser, got {losers}"

    stored = repo.get(signed.payload.release_id)
    assert stored is not None
    assert stored["consumed_by_attempt_id"] == winners[0][0]