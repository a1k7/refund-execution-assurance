"""Scenario 15 — Reconciliation resolves an unknown outcome."""
from __future__ import annotations

from app.domain.enums import OperationState, ReceiverResponseClass, TerminalClassification
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_reconciliation_resolves_unknown_to_completion(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        complete_on_accept=True,
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        op = world.operations.get(bo)
        assert op.state is OperationState.OUTCOME_UNKNOWN
        # Reference recorded from INCONCLUSIVE response (after Batch-3.3 fix).
        assert op.receiver_refund_reference is not None

        # Retry blocked before reconciliation.
        decision = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision.decision.value == "BLOCK_REQUIRE_RECONCILIATION"

        # Restore normal status reporting so reconciliation can query the truth.
        world.receiver.set_behaviour(SimulatedReceiverBehaviour(complete_on_accept=True))
        new_state = world.reconciliation.reconcile(bo)
        assert new_state is OperationState.COMPLETION_CONFIRMED

        # Verifier now agrees.
        report = world.verifier.verify(bo)
        assert report.terminal_classification is TerminalClassification.COMPLETION_CONFIRMED
        assert report.verification_status.value == "VERIFIED"

        # Further dispatches remain blocked.
        decision_after = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision_after.decision.value == "BLOCK_COMPLETION_CONFIRMED"
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_reconciliation_to_cancellation(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        op = world.operations.get(bo)
        assert op.receiver_refund_reference is not None
        # Someone else cancels the refund at the receiver.
        world.receiver.cancel(
            business_operation_id=bo,
            receiver_refund_reference=op.receiver_refund_reference,
        )
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())
        new_state = world.reconciliation.reconcile(bo)
        assert new_state is OperationState.CANCELLATION_CONFIRMED
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())