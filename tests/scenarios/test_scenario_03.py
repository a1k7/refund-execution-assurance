"""Scenario 3 — Revocation between connector's check and receiver's acceptance."""
from __future__ import annotations

from app.domain.enums import OperationState, ReceiverResponseClass
from app.connector.hooks import DispatchContext


def test_revocation_between_check_and_acceptance_is_fenced(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)

    def revoke_mid_dispatch(ctx: DispatchContext) -> None:
        # Fired *after* atomic consumption and *before* the receiver call.
        world.revocation.revoke(bo, source="scenario-03", receiver=world.receiver)

    world.hooks.on_before_receiver_call(revoke_mid_dispatch)
    try:
        result = world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
    finally:
        world.hooks.clear()

    assert result.response_class is ReceiverResponseClass.REJECTED_FENCED
    assert result.operation_state is OperationState.DISPATCH_REJECTED
    assert world.oracle.refund_count() == 0

    # Determinism check: the hook fired exactly once, no sleeps, no retries.
    events = world.ledger.read_for_operation(bo)
    types = [e["event_type"] for e in events]
    assert types.count("RELEASE_CONSUMED") == 1
    assert types.count("AUTHORITY_REVOKED") == 1
    assert "DISPATCH_REJECTED" in types