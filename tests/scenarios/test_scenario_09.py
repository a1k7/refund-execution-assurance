"""Scenario 9 — A new idempotency key cannot bypass business-operation duplicate protection."""
from __future__ import annotations

from app.domain.enums import OperationState, ReceiverResponseClass, RetryDecisionKind
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_new_idempotency_key_does_not_open_new_slot(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        first = world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_first"
        )
        assert first.operation_state is OperationState.OUTCOME_UNKNOWN
        assert world.operations.get(bo).state is OperationState.OUTCOME_UNKNOWN  # type: ignore[union-attr]

        signed2 = world.issue_release(bo)
        second = world.coordinator.dispatch(
            business_operation_id=bo, release=signed2, idempotency_key="idem_second"
        )
        assert "duplicate_guard" in (second.reason or "")
        assert "reconciliation" in (second.reason or "").lower()

        # Ground truth: exactly one refund exists (from the first inconclusive
        # dispatch); the retry did NOT create a second one.
        assert world.oracle.refund_count() == 1, world.oracle.refund_states()

        decision = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision.decision is RetryDecisionKind.BLOCK_REQUIRE_RECONCILIATION

        report = world.verifier.verify(bo)
        assert report.reconciliation_required is True
        assert report.retry_eligible is False
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())