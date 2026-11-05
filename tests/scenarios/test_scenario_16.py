"""Scenario 16 — Release epoch supersession and stale-epoch rejection."""
from __future__ import annotations

from app.domain.enums import OperationState, ReceiverResponseClass


def test_stale_epoch_rejected_by_connector(world) -> None:
    bo = world.create_operation()
    signed_old = world.issue_release(bo, release_epoch=0)
    world.revocation.supersede(bo, source="scenario-16", receiver=world.receiver)

    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed_old, idempotency_key="idem_1"
    )
    assert result.operation_state is OperationState.ADMISSIBILITY_DENIED
    assert result.reason == "stale_epoch"


def test_stale_epoch_rejected_by_receiver_as_defense_in_depth(world) -> None:
    bo = world.create_operation()
    world.revocation.supersede(bo, source="scenario-16", receiver=world.receiver)
    resp = world.receiver.accept(
        business_operation_id=bo,
        release_id="rel_direct",
        release_epoch=0,
        idempotency_key="idem_direct",
        amount=1000,
        currency="USD",
    )
    assert resp.response_class is ReceiverResponseClass.REJECTED_FENCED
    assert world.oracle.refund_count() == 0


def test_fresh_release_after_supersede_is_accepted(world) -> None:
    bo = world.create_operation()
    world.issue_release(bo, release_epoch=0)
    world.revocation.supersede(bo, source="scenario-16", receiver=world.receiver)
    # Re-issue at the new epoch.
    signed_new = world.issue_release(bo)
    assert signed_new.payload.release_epoch == 1
    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed_new, idempotency_key="idem_new"
    )
    assert result.operation_state is OperationState.IN_FLIGHT