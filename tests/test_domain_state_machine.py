"""Exhaustive check that the pure state machine agrees with its declared table."""
from __future__ import annotations

import pytest

from app.domain.enums import OperationState
from app.domain.errors import IllegalTransition
from app.domain.state_machine import (
    allowed_transitions,
    assert_transition,
    is_terminal,
)


def test_transition_table_is_exhaustive_and_consistent() -> None:
    """For every (from, to) pair, assert_transition raises iff the pair is illegal.

    Legal self-transitions are allowed *only* for OUTCOME_UNKNOWN (documented
    rule). Every other self-transition must raise.
    """
    for from_s in OperationState:
        legal_targets = allowed_transitions(from_s)
        for to_s in OperationState:
            is_declared_legal = to_s in legal_targets
            is_self = from_s is to_s
            if is_declared_legal and not (is_self and from_s is not OperationState.OUTCOME_UNKNOWN):
                assert_transition(from_s, to_s, reason="test")
            else:
                with pytest.raises(IllegalTransition):
                    assert_transition(from_s, to_s, reason="test")


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for terminal in (
        OperationState.COMPLETION_CONFIRMED,
        OperationState.CANCELLATION_CONFIRMED,
    ):
        assert is_terminal(terminal)
        assert allowed_transitions(terminal) == frozenset()


def test_outcome_unknown_is_not_terminal_and_can_only_terminalise() -> None:
    assert not is_terminal(OperationState.OUTCOME_UNKNOWN)
    # OUTCOME_UNKNOWN can be self-reentered (documented) and can reach both terminal states.
    assert OperationState.OUTCOME_UNKNOWN in allowed_transitions(OperationState.OUTCOME_UNKNOWN)
    assert OperationState.COMPLETION_CONFIRMED in allowed_transitions(OperationState.OUTCOME_UNKNOWN)
    assert OperationState.CANCELLATION_CONFIRMED in allowed_transitions(OperationState.OUTCOME_UNKNOWN)


def test_in_flight_cannot_jump_to_completion_without_going_through_evidence_paths() -> None:
    """Sanity: IN_FLIGHT -> COMPLETION_CONFIRMED is legal (evidence decides it), but
    IN_FLIGHT -> CANCELLATION_ACKNOWLEDGED is not (must go through REQUESTED)."""
    assert OperationState.COMPLETION_CONFIRMED in allowed_transitions(OperationState.IN_FLIGHT)
    assert OperationState.CANCELLATION_ACKNOWLEDGED not in allowed_transitions(OperationState.IN_FLIGHT)


def test_cancellation_acknowledged_is_not_terminal_and_cannot_skip_backwards() -> None:
    acked = OperationState.CANCELLATION_ACKNOWLEDGED
    assert not is_terminal(acked)
    assert OperationState.CANCELLATION_REQUESTED not in allowed_transitions(acked)
    assert OperationState.CANCELLATION_CONFIRMED in allowed_transitions(acked)
    assert OperationState.COMPLETION_CONFIRMED in allowed_transitions(acked)
    assert OperationState.OUTCOME_UNKNOWN in allowed_transitions(acked)