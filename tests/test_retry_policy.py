"""Retry policy: same rules as the duplicate guard, different interface."""
from __future__ import annotations

from app.domain.enums import OperationState, RetryDecisionKind, TerminalClassification


def test_retry_allowed_when_rejected(world) -> None:
    bo = world.create_operation()
    world.operations.update_state(bo, OperationState.RELEASE_ISSUED, world.clock.now())
    world.operations.update_state(bo, OperationState.RELEASE_VERIFIED, world.clock.now())
    world.operations.update_state(bo, OperationState.ADMISSIBILITY_ALLOWED, world.clock.now())
    world.operations.update_state(bo, OperationState.RELEASE_CONSUMED, world.clock.now())
    world.operations.update_state(bo, OperationState.DISPATCH_ATTEMPTED, world.clock.now())
    world.operations.update_state(bo, OperationState.DISPATCH_REJECTED, world.clock.now())

    from app.connector.retry_policy import RetryPolicy
    policy = RetryPolicy(world.operations, world.attempts)
    decision = policy.evaluate(bo)
    assert decision.decision is RetryDecisionKind.ALLOW


def test_retry_blocked_when_completed(world) -> None:
    bo = world.create_operation()
    world.operations.update_fields(
        bo, world.clock.now(),
        terminal_classification=TerminalClassification.COMPLETION_CONFIRMED,
        state=OperationState.COMPLETION_CONFIRMED,
    )
    from app.connector.retry_policy import RetryPolicy
    decision = RetryPolicy(world.operations, world.attempts).evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_COMPLETION_CONFIRMED


def test_retry_blocked_when_cancelled(world) -> None:
    bo = world.create_operation()
    world.operations.update_fields(
        bo, world.clock.now(),
        terminal_classification=TerminalClassification.CANCELLATION_CONFIRMED,
        state=OperationState.CANCELLATION_CONFIRMED,
    )
    from app.connector.retry_policy import RetryPolicy
    decision = RetryPolicy(world.operations, world.attempts).evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_REQUIRE_NEW_AUTHORIZATION


def test_retry_blocked_when_unknown(world) -> None:
    bo = world.create_operation()
    world.operations.update_fields(
        bo, world.clock.now(),
        terminal_classification=TerminalClassification.OUTCOME_UNKNOWN,
        state=OperationState.OUTCOME_UNKNOWN,
    )
    from app.connector.retry_policy import RetryPolicy
    decision = RetryPolicy(world.operations, world.attempts).evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_REQUIRE_RECONCILIATION


def test_retry_blocked_when_in_flight(world) -> None:
    bo = world.create_operation()
    world.operations.update_state(bo, OperationState.RELEASE_ISSUED, world.clock.now())
    world.operations.update_state(bo, OperationState.RELEASE_VERIFIED, world.clock.now())
    world.operations.update_state(bo, OperationState.ADMISSIBILITY_ALLOWED, world.clock.now())
    world.operations.update_state(bo, OperationState.RELEASE_CONSUMED, world.clock.now())
    world.operations.update_state(bo, OperationState.DISPATCH_ATTEMPTED, world.clock.now())
    world.operations.update_state(bo, OperationState.DISPATCH_ACCEPTED, world.clock.now())
    world.operations.update_state(bo, OperationState.IN_FLIGHT, world.clock.now())

    from app.connector.retry_policy import RetryPolicy
    decision = RetryPolicy(world.operations, world.attempts).evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_IN_FLIGHT


def test_retry_blocked_when_revoked(world) -> None:
    bo = world.create_operation()
    world.revocation.revoke(bo, source="test", receiver=world.receiver)
    world.operations.update_state(bo, OperationState.RELEASE_ISSUED, world.clock.now())
    world.operations.update_state(bo, OperationState.RELEASE_VERIFIED, world.clock.now())
    world.operations.update_state(bo, OperationState.ADMISSIBILITY_DENIED, world.clock.now())

    from app.connector.retry_policy import RetryPolicy
    decision = RetryPolicy(world.operations, world.attempts).evaluate(bo)
    assert decision.decision is RetryDecisionKind.BLOCK_AUTHORITY_REVOKED