"""Scenario 7 — Receiver actually completed, but reported INCONCLUSIVE."""
from __future__ import annotations

from app.domain.enums import (
    OperationState,
    ReceiverResponseClass,
    TerminalClassification,
)
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_verifier_does_not_infer_hidden_completion(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        complete_on_accept=True,
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        result = world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        assert result.operation_state is OperationState.OUTCOME_UNKNOWN

        # ORACLE: ground truth is COMPLETED — but only the oracle can see this.
        assert world.oracle.refund_count() == 1
        only_refund = list(world.oracle.all_refunds().values())[0]
        assert only_refund.state.value == "COMPLETED"

        # VERIFIER: sees only evidence, correctly returns OUTCOME_UNKNOWN.
        report = world.verifier.verify(bo)
        assert report.terminal_classification is TerminalClassification.OUTCOME_UNKNOWN
        assert report.retry_eligible is False
        assert report.reconciliation_required is True

        # Retry blocked pending reconciliation.
        decision = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision.decision.value == "BLOCK_REQUIRE_RECONCILIATION"
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())