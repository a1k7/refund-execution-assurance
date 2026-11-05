"""Revocation policy: BLOCK_NEW_DISPATCHES (default) vs REQUEST_CANCELLATION_OF_IN_FLIGHT."""
from __future__ import annotations

import pytest

from app.domain.enums import InFlightPolicy, OperationState, ReceiverResponseClass
from app.domain.errors import ReceiverCapabilityError
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_default_policy_is_block_new_dispatches(world) -> None:
    bo = world.create_operation()
    op = world.operations.get(bo)
    assert op is not None
    assert op.in_flight_policy is InFlightPolicy.BLOCK_NEW_DISPATCHES


def test_default_policy_does_not_auto_cancel(world) -> None:
    """After revocation with default policy, no cancellation is triggered."""
    bo = world.create_operation()
    signed = world.issue_release(bo)
    world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    world.revocation.revoke(bo, source="test", receiver=world.receiver)
    op = world.operations.get(bo)
    assert op is not None
    assert op.state is OperationState.IN_FLIGHT  # NOT CANCELLATION_REQUESTED


def test_revocation_blocks_new_dispatch_even_with_valid_release(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    world.revocation.revoke(bo, source="test", receiver=world.receiver)

    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    assert result.operation_state is OperationState.ADMISSIBILITY_DENIED
    assert result.reason == "authority_revoked"


def test_revocation_rejects_when_receiver_lacks_cancellation_capability(world) -> None:
    """Requesting cancellation with an incapable receiver must fail closed."""
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        supports_cancellation=False, supports_status_query=False,
    ))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        with pytest.raises(ReceiverCapabilityError):
            world.cancellation.request_cancellation(bo)
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_revocation_rejects_when_receiver_lacks_status_query(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        supports_cancellation=True, supports_status_query=False,
    ))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        with pytest.raises(ReceiverCapabilityError):
            world.cancellation.request_cancellation(bo)
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_supersede_does_not_revoke_authority(world) -> None:
    bo = world.create_operation()
    world.revocation.supersede(bo, source="test", receiver=world.receiver)
    op = world.operations.get(bo)
    assert op is not None
    assert op.authority_state.value == "ACTIVE"
    assert op.current_epoch == 1


def test_revocation_increments_epoch(world) -> None:
    bo = world.create_operation()
    assert world.operations.get(bo).current_epoch == 0  # type: ignore[union-attr]
    world.revocation.revoke(bo, source="test", receiver=world.receiver)
    assert world.operations.get(bo).current_epoch == 1  # type: ignore[union-attr]
    world.revocation.revoke(bo, source="test", receiver=world.receiver)
    assert world.operations.get(bo).current_epoch == 2  # type: ignore[union-attr]