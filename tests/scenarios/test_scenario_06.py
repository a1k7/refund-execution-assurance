"""Scenario 6 — Cancellation acknowledged, terminal state unknown."""
from __future__ import annotations

from app.domain.enums import (
    InFlightPolicy,
    OperationState,
    ReceiverResponseClass,
    TerminalClassification,
)
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_cancellation_ack_with_unknown_terminal(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        cancel_transitions_to_terminal=True,
        status_class_override=ReceiverResponseClass.STATUS_UNKNOWN,
    ))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = world.issue_release(bo)
        dispatch = world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        assert dispatch.operation_state is OperationState.IN_FLIGHT

        world.revocation.revoke(bo, source="scenario-06", receiver=world.receiver)
        final_state = world.cancellation.request_cancellation(bo)
        assert final_state is OperationState.OUTCOME_UNKNOWN

        op = world.operations.get(bo)
        assert op is not None
        assert op.reconciliation_required is True
        assert op.retry_eligible is False

        report = world.verifier.verify(bo)
        assert report.terminal_classification is TerminalClassification.OUTCOME_UNKNOWN
        assert report.reconciliation_required is True
        assert report.retry_eligible is False

        # Duplicate guard blocks a retry with a fresh key.
        decision = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision.decision.value == "BLOCK_REQUIRE_RECONCILIATION"
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())