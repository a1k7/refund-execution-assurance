"""Pure, deterministic state machine. Every legal transition is listed explicitly.

Design notes:
  * This module has no I/O and no side effects.
  * Illegal transitions raise IllegalTransition and MUST be recorded by callers as
    GUARD_VIOLATED evidence events (the verifier flags those as failures).
  * Self-transitions are permitted only for OUTCOME_UNKNOWN.
"""
from __future__ import annotations

from typing import Dict, FrozenSet

from .enums import OperationState as S
from .errors import IllegalTransition

_TERMINAL: FrozenSet[S] = frozenset({S.COMPLETION_CONFIRMED, S.CANCELLATION_CONFIRMED})

_ALLOWED: Dict[S, FrozenSet[S]] = {
    S.CREATED: frozenset({S.RELEASE_ISSUED}),
    S.RELEASE_ISSUED: frozenset({S.RELEASE_VERIFIED, S.ADMISSIBILITY_DENIED}),
    S.RELEASE_VERIFIED: frozenset({S.ADMISSIBILITY_ALLOWED, S.ADMISSIBILITY_DENIED}),
    S.ADMISSIBILITY_ALLOWED: frozenset({S.RELEASE_CONSUMED, S.ADMISSIBILITY_DENIED}),
    S.ADMISSIBILITY_DENIED: frozenset({S.RELEASE_ISSUED, S.OUTCOME_UNKNOWN}),
    S.RELEASE_CONSUMED: frozenset({S.DISPATCH_ATTEMPTED}),
    S.DISPATCH_ATTEMPTED: frozenset({S.DISPATCH_REJECTED, S.DISPATCH_ACCEPTED, S.OUTCOME_UNKNOWN}),
    # UPDATED: allow DISPATCH_REJECTED to go to ADMISSIBILITY_DENIED on a blocked retry.
    S.DISPATCH_REJECTED: frozenset({S.RELEASE_ISSUED, S.OUTCOME_UNKNOWN, S.ADMISSIBILITY_DENIED}),
    S.DISPATCH_ACCEPTED: frozenset(
        {S.IN_FLIGHT, S.COMPLETION_CONFIRMED, S.CANCELLATION_REQUESTED, S.OUTCOME_UNKNOWN}
    ),
    S.IN_FLIGHT: frozenset({S.COMPLETION_CONFIRMED, S.CANCELLATION_REQUESTED, S.OUTCOME_UNKNOWN}),
    S.CANCELLATION_REQUESTED: frozenset(
        {S.CANCELLATION_ACKNOWLEDGED, S.CANCELLATION_CONFIRMED, S.COMPLETION_CONFIRMED, S.OUTCOME_UNKNOWN}
    ),
    S.CANCELLATION_ACKNOWLEDGED: frozenset(
        {S.CANCELLATION_CONFIRMED, S.COMPLETION_CONFIRMED, S.OUTCOME_UNKNOWN}
    ),
    S.OUTCOME_UNKNOWN: frozenset({S.COMPLETION_CONFIRMED, S.CANCELLATION_CONFIRMED, S.OUTCOME_UNKNOWN}),
    S.COMPLETION_CONFIRMED: frozenset(),
    S.CANCELLATION_CONFIRMED: frozenset(),
}


def is_terminal(state: OperationState) -> bool:
    return state in _TERMINAL


def allowed_transitions(state: OperationState) -> FrozenSet[OperationState]:
    return _ALLOWED.get(state, frozenset())


def assert_transition(from_state: OperationState, to_state: OperationState, reason: str = "") -> None:
    if from_state == to_state:
        if from_state is S.OUTCOME_UNKNOWN:
            return
        raise IllegalTransition(f"Illegal self-transition {from_state.value}: {reason}")
    if to_state not in _ALLOWED.get(from_state, frozenset()):
        raise IllegalTransition(
            f"Illegal transition {from_state.value} -> {to_state.value}: {reason or 'no reason'}"
        )