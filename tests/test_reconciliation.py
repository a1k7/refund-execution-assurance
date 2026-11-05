"""Reconciliation: resolves OUTCOME_UNKNOWN to a terminal conclusion."""
from __future__ import annotations

import pytest

from app.domain.enums import OperationState, ReceiverResponseClass, TerminalClassification
from app.domain.errors import BadRequestError, NotFoundError
from app.receiver.simulated import SimulatedReceiverBehaviour


def _to_outcome_unknown(world, complete: bool = False):
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        complete_on_accept=complete,
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
        return bo
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour(complete_on_accept=complete))


def test_reconcile_resolves_to_completion(world) -> None:
    bo = _to_outcome_unknown(world, complete=True)
    state = world.reconciliation.reconcile(bo)
    assert state is OperationState.COMPLETION_CONFIRMED
    op = world.operations.get(bo)
    assert op is not None
    assert op.terminal_classification is TerminalClassification.COMPLETION_CONFIRMED
    assert op.reconciliation_required is False


def test_reconcile_resolves_to_cancellation(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        # Some other actor cancels the refund at the receiver.
        op = world.operations.get(bo)
        assert op is not None
        assert op.receiver_refund_reference is not None
        world.receiver.cancel(
            business_operation_id=bo,
            receiver_refund_reference=op.receiver_refund_reference,
        )
        # Restore normal status reporting so reconcile can see terminal state.
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())
        state = world.reconciliation.reconcile(bo)
        assert state is OperationState.CANCELLATION_CONFIRMED
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_reconcile_inconclusive_status_stays_unknown(world) -> None:
    bo = _to_outcome_unknown(world, complete=False)
    # Restore status to PENDING (inconclusive).
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        status_class_override=ReceiverResponseClass.STATUS_UNKNOWN,
    ))
    try:
        state = world.reconciliation.reconcile(bo)
        assert state is OperationState.OUTCOME_UNKNOWN
        op = world.operations.get(bo)
        assert op is not None
        assert op.reconciliation_required is True
        assert op.retry_eligible is False
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_reconcile_non_unknown_state_raises(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    with pytest.raises(BadRequestError):
        world.reconciliation.reconcile(bo)


def test_reconcile_terminal_state_is_noop(world) -> None:
    bo = _to_outcome_unknown(world, complete=True)
    world.reconciliation.reconcile(bo)  # -> COMPLETION_CONFIRMED
    state = world.reconciliation.reconcile(bo)  # idempotent
    assert state is OperationState.COMPLETION_CONFIRMED


def test_reconcile_unknown_operation_raises(world) -> None:
    with pytest.raises(NotFoundError):
        world.reconciliation.reconcile("bo_missing")


def test_verifier_updates_after_reconciliation(world) -> None:
    bo = _to_outcome_unknown(world, complete=True)
    before = world.verifier.verify(bo)
    assert before.terminal_classification is TerminalClassification.OUTCOME_UNKNOWN
    world.reconciliation.reconcile(bo)
    after = world.verifier.verify(bo)
    assert after.terminal_classification is TerminalClassification.COMPLETION_CONFIRMED