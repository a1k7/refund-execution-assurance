"""Business-operation-level duplicate guard.

The unit of duplication is ``business_operation_id``, not the idempotency key.
A new idempotency key NEVER opens a new dispatch slot for the same business
operation. The guard reasons from the operation's recorded state and (where
useful) the last dispatch attempt.

Design note — authority vs. duplicate
-------------------------------------
The guard answers *two* related questions for retry-eligibility:
  1. Is this a duplicate or in-flight attempt? (duplication)
  2. Is the underlying authority revoked? (authorization)

For (2), the coordinator ALSO has a dedicated authority gate that records
``ADMISSIBILITY_DENIED`` with the correct evidence when it fires first.
The guard's authority check exists for direct callers — e.g. the
``/operations/{id}`` endpoint and ``RetryPolicy`` — that need a complete
retry-decision answer without driving the full dispatch pipeline.
"""
from __future__ import annotations

from ..clock import Clock
from ..domain.enums import (
    AuthorityState,
    OperationState,
    RetryDecisionKind,
    TerminalClassification,
)
from ..domain.errors import NotFoundError
from ..domain.models import RetryDecision
from ..persistence.db import Database
from ..persistence.repositories import (
    DispatchAttemptRepository,
    OperationRepository,
)


class DuplicateGuard:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock
        self.ops = OperationRepository(db)
        self.attempts = DispatchAttemptRepository(db)

    def evaluate(self, business_operation_id: str) -> RetryDecision:
        op = self.ops.get(business_operation_id)
        if op is None:
            raise NotFoundError(business_operation_id)

        # Terminal / in-flight / unknown states dominate over authority state:
        # the operation is already in a shape that blocks a new dispatch
        # regardless of authority.
        if op.terminal_classification == TerminalClassification.COMPLETION_CONFIRMED:
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_COMPLETION_CONFIRMED,
                reason="operation already completed; a new refund requires a new business decision",
            )
        if op.terminal_classification == TerminalClassification.CANCELLATION_CONFIRMED:
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_REQUIRE_NEW_AUTHORIZATION,
                reason="cancellation confirmed; a new refund requires explicit re-authorization",
            )
        if op.state is OperationState.OUTCOME_UNKNOWN:
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_REQUIRE_RECONCILIATION,
                reason="previous attempt outcome is UNKNOWN; retry blocked pending reconciliation",
            )
        if op.state in (
            OperationState.DISPATCH_ACCEPTED,
            OperationState.IN_FLIGHT,
            OperationState.CANCELLATION_REQUESTED,
            OperationState.CANCELLATION_ACKNOWLEDGED,
        ):
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_IN_FLIGHT,
                reason=f"operation is {op.state.value}; a second dispatch would risk a duplicate refund",
            )

        # Only after confirming no duplicate/in-flight condition exists,
        # check authority. This ordering ensures that when the coordinator
        # does reach its authority gate first, this branch is only hit by
        # direct callers such as /operations/{id}.
        if op.authority_state is AuthorityState.REVOKED:
            return RetryDecision(
                decision=RetryDecisionKind.BLOCK_AUTHORITY_REVOKED,
                reason="authority is revoked; new dispatches are blocked",
            )

        return RetryDecision(
            decision=RetryDecisionKind.ALLOW,
            reason="no duplicate or in-flight attempt detected",
        )