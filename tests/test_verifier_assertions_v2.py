"""Second-pass regression tests for verifier assertions (PR #2)."""
import pytest
from datetime import datetime, timezone
from app.domain.enums import (
    EvidenceEventType,
    OperationState,
    ReceiverResponseClass,
)
from app.domain.models import EvidenceEvent
from app.verification.assertions import (
    assert_cancellation_confirmed_has_terminal_evidence,
    assert_release_verified_before_dispatch,
    assert_release_consumed_before_dispatch_attempted,
    assert_retries_justified,
)

NOW = datetime.now(timezone.utc)

def _ev(etype, state=None, resp=None, rel_id="rel_1", ref="ref_1", op_id="bo_1", att_id=None, seq=None):
    return EvidenceEvent(
        event_id=f"evt_{etype.value}",
        event_type=etype,
        business_operation_id=op_id,
        release_id=rel_id,
        dispatch_attempt_id=att_id,
        receiver_refund_reference=ref,
        timestamp=NOW,
        new_state=state,
        receiver_response_class=resp,
        evidence_source="test",
        sequence=seq,
    )

# --- Finding 1: Temporal Cancellation ---
def test_cancellation_confirmation_cannot_precede_ack():
    events = [
        _ev(EvidenceEventType.CANCELLATION_CONFIRMED, state=OperationState.CANCELLATION_CONFIRMED, resp=ReceiverResponseClass.STATUS_TERMINAL_CANCELLED),
        _ev(EvidenceEventType.CANCELLATION_ACKNOWLEDGED),
    ]
    assert assert_cancellation_confirmed_has_terminal_evidence(events) is not None

def test_cancellation_cross_operation_fails():
    events = [
        _ev(EvidenceEventType.CANCELLATION_REQUESTED, op_id="bo_1"),
        _ev(EvidenceEventType.CANCELLATION_CONFIRMED, op_id="bo_2", state=OperationState.CANCELLATION_CONFIRMED, resp=ReceiverResponseClass.STATUS_TERMINAL_CANCELLED),
    ]
    assert assert_cancellation_confirmed_has_terminal_evidence(events) is not None

def test_cancellation_wrong_attempt_fails():
    events = [
        _ev(EvidenceEventType.CANCELLATION_REQUESTED, att_id="att_1"),
        _ev(EvidenceEventType.CANCELLATION_CONFIRMED, att_id="att_2", state=OperationState.CANCELLATION_CONFIRMED, resp=ReceiverResponseClass.STATUS_TERMINAL_CANCELLED),
    ]
    assert assert_cancellation_confirmed_has_terminal_evidence(events) is not None

# --- Finding 2: Attempt-Specific Ordering ---
def test_release_verified_for_wrong_attempt_fails():
    events = [
        _ev(EvidenceEventType.RELEASE_VERIFIED, att_id="att_1"),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, att_id="att_2"),
    ]
    assert assert_release_verified_before_dispatch(events) is not None

def test_release_consumed_for_wrong_attempt_fails():
    events = [
        _ev(EvidenceEventType.RELEASE_CONSUMED, att_id="att_1"),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, att_id="att_2"),
    ]
    assert assert_release_consumed_before_dispatch_attempted(events) is not None

# --- Finding 3: Retry Justification ---
def test_retry_after_fenced_rejection_without_fresh_release_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_1", att_id="att_1"),
        _ev(EvidenceEventType.DISPATCH_REJECTED, rel_id="rel_1", att_id="att_1", state=OperationState.DISPATCH_REJECTED, resp=ReceiverResponseClass.REJECTED_FENCED),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_1", att_id="att_2"), # <--- Same release!
    ]
    assert assert_retries_justified(events) is not None

def test_retry_after_cancellation_ack_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, att_id="att_1"),
        _ev(EvidenceEventType.CANCELLATION_ACKNOWLEDGED, att_id="att_1", state=OperationState.CANCELLATION_ACKNOWLEDGED),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, att_id="att_2"),
    ]
    assert assert_retries_justified(events) is not None

def test_retry_after_fenced_rejection_with_fresh_release_passes():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_1", att_id="att_1"),
        _ev(EvidenceEventType.DISPATCH_REJECTED, rel_id="rel_1", att_id="att_1", state=OperationState.DISPATCH_REJECTED, resp=ReceiverResponseClass.REJECTED_FENCED),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_2", att_id="att_2"), # <--- Fresh release!
    ]
    assert assert_retries_justified(events) is None