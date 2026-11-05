"""Scenario 13 — Receiver without cancellation / status-query capability."""
from __future__ import annotations

import pytest

from app.domain.enums import InFlightPolicy
from app.domain.errors import ReceiverCapabilityError
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_cancellation_config_rejected_when_receiver_lacks_capability(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        supports_cancellation=False,
        supports_status_query=False,
    ))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        world.revocation.revoke(bo, source="scenario-13", receiver=world.receiver)

        with pytest.raises(ReceiverCapabilityError):
            world.cancellation.request_cancellation(bo)

        # No cancellation request was sent to the receiver.
        events = world.ledger.read_for_operation(bo)
        types = [e["event_type"] for e in events]
        assert "CANCELLATION_REQUESTED" not in types
        assert "CANCELLATION_CONFIRMED" not in types

        # Receiver ground truth: refund still in-flight, never cancelled.
        refunds = world.oracle.refunds_for_operation(bo)
        assert len(refunds) == 1
        assert refunds[0].state.value == "IN_FLIGHT"
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_cancellation_config_rejected_when_status_query_unsupported(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        supports_cancellation=True,
        supports_status_query=False,
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