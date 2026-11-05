"""Scenario 1 — Revocation before dispatch."""
from __future__ import annotations

from app.domain.enums import OperationState, RetryDecisionKind


def test_revocation_before_dispatch_blocks(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    world.revocation.revoke(bo, source="scenario-01", receiver=world.receiver)

    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )

    assert result.operation_state is OperationState.ADMISSIBILITY_DENIED
    assert result.reason == "authority_revoked"
    assert world.oracle.refund_count() == 0
    assert world.oracle.refunds_for_operation(bo) == []

    decision = world.coordinator.duplicate_guard.evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_AUTHORITY_REVOKED

    assert world.ledger.verify_chain() is True
    events = world.ledger.read_for_operation(bo)
    types = [e["event_type"] for e in events]
    assert "AUTHORITY_REVOKED" in types
    assert "ADMISSIBILITY_DENIED" in types