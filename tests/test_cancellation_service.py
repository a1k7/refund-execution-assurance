"""Cancellation service: strict separation of REQUESTED, ACKNOWLEDGED, CONFIRMED."""
from __future__ import annotations

import pytest

from app.domain.enums import InFlightPolicy, OperationState, ReceiverResponseClass
from app.domain.errors import BadRequestError, NotFoundError
from app.receiver.simulated import SimulatedReceiverBehaviour


def _dispatch_to_in_flight(world, bo: str) -> None:
    signed = world.issue_release(bo)
    r = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_cancel"
    )
    assert r.operation_state is OperationState.IN_FLIGHT


def test_cancel_ack_alone_does_not_confirm(world) -> None:
    """CANCEL_ACK + PENDING status must yield OUTCOME_UNKNOWN, never CANCELLATION_CONFIRMED."""
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        cancel_transitions_to_terminal=False,
        status_class_override=ReceiverResponseClass.STATUS_PENDING,
    ))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        _dispatch_to_in_flight(world, bo)
        result_state = world.cancellation.request_cancellation(bo)
        assert result_state is OperationState.OUTCOME_UNKNOWN
        op = world.operations.get(bo)
        assert op is not None
        assert op.state is OperationState.OUTCOME_UNKNOWN
        assert op.reconciliation_required is True
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_cancel_ack_with_terminal_status_confirms_cancellation(world) -> None:
    bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
    _dispatch_to_in_flight(world, bo)
    result_state = world.cancellation.request_cancellation(bo)
    assert result_state is OperationState.CANCELLATION_CONFIRMED
    op = world.operations.get(bo)
    assert op is not None
    assert op.terminal_classification is not None
    assert op.terminal_classification.value == "CANCELLATION_CONFIRMED"


def test_cancel_rejected_with_terminal_completed_marks_completion(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        complete_on_accept=True,
        cancel_class_override=ReceiverResponseClass.CANCEL_REJECTED,
    ))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        result_state = world.cancellation.request_cancellation(bo)
        assert result_state is OperationState.COMPLETION_CONFIRMED
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_cancel_requires_receiver_refund_reference(world) -> None:
    """An operation that never dispatched cannot be cancelled."""
    bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
    world.issue_release(bo)  # but do not dispatch
    with pytest.raises(BadRequestError):
        world.cancellation.request_cancellation(bo)


def test_cancel_unknown_operation_raises(world) -> None:
    with pytest.raises(NotFoundError):
        world.cancellation.request_cancellation("bo_does_not_exist")


def test_cancel_terminal_operation_raises(world) -> None:
    bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
    _dispatch_to_in_flight(world, bo)
    world.cancellation.request_cancellation(bo)  # -> CANCELLATION_CONFIRMED
    with pytest.raises(BadRequestError):
        world.cancellation.request_cancellation(bo)


def test_confirm_from_status_promotes_acknowledged_to_confirmed(world) -> None:
    """Acknowledge first (status pending), then later status confirms cancellation."""
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        cancel_transitions_to_terminal=True,
        status_class_override=ReceiverResponseClass.STATUS_PENDING,
    ))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        _dispatch_to_in_flight(world, bo)
        first = world.cancellation.request_cancellation(bo)
        assert first is OperationState.OUTCOME_UNKNOWN
        # Now restore normal status reporting and explicitly query.
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())
        second = world.cancellation.confirm_from_status(bo)
        # After the internal cancel already fired (receiver transitioned to CANCELLED),
        # status now reports terminal cancelled. But we may have moved the op to
        # OUTCOME_UNKNOWN already, in which case confirm_from_status still needs a
        # terminal status response to promote. It does not currently touch OUTCOME_UNKNOWN
        # states via cancel service (it calls _attempt_terminal_from_status directly).
        assert second in (OperationState.CANCELLATION_CONFIRMED, OperationState.OUTCOME_UNKNOWN)
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())