"""Retry policy derived from the operation's current state and terminal classification.

A RetryDecision is always recorded as evidence so the decision path is auditable.
The policy mirrors the duplicate guard's rules but presents a caller-facing
"can I retry?" answer that the API exposes without exposing internal state.
"""
from __future__ import annotations

from ..domain.enums import (
    AuthorityState,
    OperationState,
    RetryDecisionKind,
    TerminalClassification,
)
from ..domain.models import RetryDecision
from ..persistence.repositories import (
    DispatchAttemptRepository,
    OperationRepository,
)


class RetryPolicy:
    def __init__(
        self,
        operations: OperationRepository,
        attempts: DispatchAttemptRepository,
    ) -> None:
        self.ops = operations
        self.attempts = attempts

    def evaluate(self, business_operation_id: str) -> RetryDecision:
        op = self.ops.get(business_operation_id)
        if op is None:
            raise KeyError(business_operation_id)

        if op.terminal_classification == TerminalClassification.COMPLETION_CONFIRMED:
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_COMPLETION_CONFIRMED,
                reason="completion confirmed",
            )
        if op.terminal_classification == TerminalClassification.CANCELLATION_CONFIRMED:
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_REQUIRE_NEW_AUTHORIZATION,
                reason="cancellation confirmed; new authorization required",
            )
        if op.state is OperationState.OUTCOME_UNKNOWN:
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_REQUIRE_RECONCILIATION,
                reason="unknown outcome; reconciliation required before retry",
            )
        if op.state in (
            OperationState.DISPATCH_ACCEPTED,
            OperationState.IN_FLIGHT,
            OperationState.CANCELLATION_REQUESTED,
            OperationState.CANCELLATION_ACKNOWLEDGED,
        ):
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_IN_FLIGHT,
                reason=f"operation is {op.state.value}",
            )
        if op.authority_state is AuthorityState.REVOKED:
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_AUTHORITY_REVOKED,
                reason="authority revoked",
            )
        return RetryDecision(decision=RetryDecisionKind.ALLOW, reason="retry permitted")