"""Scenario 8 — Default policy: no automatic cancellation, new dispatches blocked."""
from __future__ import annotations

from app.domain.enums import InFlightPolicy, OperationState


def test_default_policy_leaves_in_flight_operation_untouched(world) -> None:
    bo = world.create_operation()  # default policy
    assert world.operations.get(bo).in_flight_policy is InFlightPolicy.BLOCK_NEW_DISPATCHES  # type: ignore[union-attr]

    signed = world.issue_release(bo)
    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    assert result.operation_state is OperationState.IN_FLIGHT

    world.revocation.revoke(bo, source="scenario-08", receiver=world.receiver)

    op = world.operations.get(bo)
    assert op is not None
    assert op.state is OperationState.IN_FLIGHT  # NOT CANCELLATION_REQUESTED

    # No cancellation control action was attempted.
    events = world.ledger.read_for_operation(bo)
    types = [e["event_type"] for e in events]
    assert "CANCELLATION_REQUESTED" not in types
    assert "CANCELLATION_ACKNOWLEDGED" not in types
    assert "CANCELLATION_CONFIRMED" not in types

    # Receiver ground truth: one in-flight refund, untouched.
    refunds = world.oracle.refunds_for_operation(bo)
    assert len(refunds) == 1
    assert refunds[0].state.value == "IN_FLIGHT"