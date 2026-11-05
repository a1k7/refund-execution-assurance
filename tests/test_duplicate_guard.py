"""Business-operation-level duplicate guard."""
from __future__ import annotations

from app.domain.enums import (
    InFlightPolicy,
    OperationState,
    RetryDecisionKind,
    TerminalClassification,
)
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_first_attempt_is_allowed(world) -> None:
    bo = world.create_operation()
    world.issue_release(bo)
    decision = world.coordinator.duplicate_guard.evaluate(bo)
    assert decision.decision is RetryDecisionKind.ALLOW


def test_dispatch_in_flight_blocks_retry(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    decision = world.coordinator.duplicate_guard.evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_IN_FLIGHT


def test_outcome_unknown_blocks_retry_with_reconciliation(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=__import__("app.domain.enums", fromlist=["ReceiverResponseClass"]).ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        decision = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision.decision is RetryDecisionKind.BLOCK_REQUIRE_RECONCILIATION
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_completion_confirmed_blocks_retry(world) -> None:
    bo = world.create_operation()
    world.operations.update_fields(
        bo, world.clock.now(),
        terminal_classification=TerminalClassification.COMPLETION_CONFIRMED,
        state=OperationState.COMPLETION_CONFIRMED,
    )
    decision = world.coordinator.duplicate_guard.evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_COMPLETION_CONFIRMED


def test_cancellation_confirmed_blocks_retry_with_new_authorization(world) -> None:
    bo = world.create_operation()
    world.operations.update_fields(
        bo, world.clock.now(),
        terminal_classification=TerminalClassification.CANCELLATION_CONFIRMED,
        state=OperationState.CANCELLATION_CONFIRMED,
    )
    decision = world.coordinator.duplicate_guard.evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_REQUIRE_NEW_AUTHORIZATION


def test_rejected_attempt_allows_retry(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=__import__("app.domain.enums", fromlist=["ReceiverResponseClass"]).ReceiverResponseClass.REJECTED_INVALID,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        # op.state is DISPATCH_REJECTED - guard allows a new attempt.
        decision = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision.decision is RetryDecisionKind.ALLOW
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_new_idempotency_key_cannot_bypass_guard(world) -> None:
    """After OUTCOME_UNKNOWN, a *different* idempotency_key still routes to reconciliation."""
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=__import__("app.domain.enums", fromlist=["ReceiverResponseClass"]).ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_original"
        )
        decision = world.coordinator.duplicate_guard.evaluate(bo)
        assert decision.decision is RetryDecisionKind.BLOCK_REQUIRE_RECONCILIATION
        assert "idempotency" not in decision.reason.lower() or "reconcil" in decision.reason.lower()
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_unknown_operation_raises(world) -> None:
    import pytest
    from app.domain.errors import NotFoundError
    with pytest.raises(NotFoundError):
        world.coordinator.duplicate_guard.evaluate("bo_does_not_exist")