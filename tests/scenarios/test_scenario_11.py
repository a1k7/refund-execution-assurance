"""Scenario 11 — Replay of a previously consumed release is rejected."""
from __future__ import annotations

from app.domain.enums import OperationState, ReceiverResponseClass
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_replay_of_consumed_release_is_rejected(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=ReceiverResponseClass.REJECTED_INVALID,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)

        first = world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_first"
        )
        assert first.operation_state is OperationState.DISPATCH_REJECTED

        # Same release, different idempotency key: duplicate guard permits (previous
        # attempt is DISPATCH_REJECTED, which is safe to retry), but atomic
        # consumption then fails because the release is already consumed.
        replay = world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_replay"
        )
        assert replay.reason == "release_already_consumed", replay.reason

        # Receiver was called only once for this release.
        assert world.oracle.refund_count() == 0

        # Evidence: exactly one RELEASE_CONSUMED for this release.
        rows = world.ledger.read_for_operation(bo)
        consumed = [r for r in rows if r["event_type"] == "RELEASE_CONSUMED"
                    and r.get("release_id") == signed.payload.release_id]
        assert len(consumed) == 1

        # A RELEASE_REPLAY_ATTEMPTED event was recorded.
        types = [r["event_type"] for r in rows]
        assert "RELEASE_REPLAY_ATTEMPTED" in types
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())