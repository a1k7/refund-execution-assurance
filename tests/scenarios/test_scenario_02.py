"""Scenario 2 — Revocation after release issuance but before connector check."""
from __future__ import annotations

from app.domain.enums import OperationState


def test_revocation_after_issue_before_connector_check(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    # Intermediate step: revocation happens *after* issuance but *before* dispatch.
    world.revocation.revoke(bo, source="scenario-02", receiver=world.receiver)

    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )

    assert result.operation_state is OperationState.ADMISSIBILITY_DENIED
    assert result.reason == "authority_revoked"
    assert world.oracle.refund_count() == 0

    events = world.ledger.read_for_operation(bo)
    types = [e["event_type"] for e in events]
    # The dispatch attempt was *not* created before denial — the ADMISSIBILITY_DENIED
    # event must come after RELEASE_VERIFIED but before any DISPATCH_ACCEPTED.
    assert "RELEASE_VERIFIED" in types
    assert "ADMISSIBILITY_DENIED" in types
    assert "DISPATCH_ACCEPTED" not in types