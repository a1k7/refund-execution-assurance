"""Scenario 5 — Completion before cancellation."""
from __future__ import annotations

from app.domain.enums import (
    InFlightPolicy,
    OperationState,
    TerminalClassification,
)
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_completion_before_cancellation_wins(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(complete_on_accept=True))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = world.issue_release(bo)
        dispatch = world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        assert dispatch.operation_state is OperationState.IN_FLIGHT
        ref = dispatch.receiver_refund_reference
        assert ref is not None

        # Ground truth: receiver already completed.
        assert world.oracle.refund_record(ref).state.value == "COMPLETED"

        world.revocation.revoke(bo, source="scenario-05", receiver=world.receiver)
        final_state = world.cancellation.request_cancellation(bo)
        assert final_state is OperationState.COMPLETION_CONFIRMED

        report = world.verifier.verify(bo)
        assert report.terminal_classification is TerminalClassification.COMPLETION_CONFIRMED
        assert report.retry_eligible is False
        assert report.reconciliation_required is False

        # Retry must remain blocked because completion is confirmed.
        decision = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision.decision.value == "BLOCK_COMPLETION_CONFIRMED"
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())