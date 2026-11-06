"""Direct unit tests for the verifier assertions (PR #2 Findings)."""
from datetime import datetime, timezone
from app.domain.enums import (
    EvidenceEventType,
    OperationState,
    ReceiverResponseClass,
)
from app.domain.models import EvidenceEvent
from app.verification.assertions import (
    assert_cancellation_ack_not_treated_as_confirmed,
    assert_release_verified_before_dispatch,
    assert_release_consumed_before_dispatch_attempted,
    assert_retries_justified,
)

NOW = datetime.now(timezone.utc)

def _ev(etype, state=None, resp=None, rel_id="rel_1", ref="ref_1"):
    return EvidenceEvent(
        event_id=f"evt_{etype.value}_{NOW.timestamp()}",
        event_type=etype,
        business_operation_id="bo_1",
        release_id=rel_id,
        receiver_refund_reference=ref,
        timestamp=NOW,
        new_state=state,
        receiver_response_class=resp,
        evidence_source="test",
    )

# --- Finding A: Cancellation ACK vs Confirmation ---
def test_ack_alone_does_not_confirm():
    events = [
        _ev(EvidenceEventType.CANCELLATION_REQUESTED, ref="ref_1"),
        _ev(EvidenceEventType.CANCELLATION_ACKNOWLEDGED, ref="ref_1"),
        _ev(EvidenceEventType.CANCELLATION_CONFIRMED, state=OperationState.CANCELLATION_CONFIRMED, resp=None, ref="ref_1"),
    ]
    # Must fail because it lacks STATUS_TERMINAL_CANCELLED
    assert assert_cancellation_ack_not_treated_as_confirmed(events) is not None

def test_confirmed_with_terminal_evidence_passes():
    events = [
        _ev(EvidenceEventType.CANCELLATION_REQUESTED, ref="ref_1"),
        _ev(EvidenceEventType.CANCELLATION_ACKNOWLEDGED, ref="ref_1"),
        _ev(EvidenceEventType.CANCELLATION_CONFIRMED, state=OperationState.CANCELLATION_CONFIRMED, resp=ReceiverResponseClass.STATUS_TERMINAL_CANCELLED, ref="ref_1"),
    ]
    assert assert_cancellation_ack_not_treated_as_confirmed(events) is None

def test_confirmed_with_uncorrelated_ref_fails():
    events = [
        _ev(EvidenceEventType.CANCELLATION_REQUESTED, ref="ref_A"),
        _ev(EvidenceEventType.CANCELLATION_ACKNOWLEDGED, ref="ref_A"),
        _ev(EvidenceEventType.CANCELLATION_CONFIRMED, state=OperationState.CANCELLATION_CONFIRMED, resp=ReceiverResponseClass.STATUS_TERMINAL_CANCELLED, ref="ref_B"),
    ]
    # Must fail because ref_B was never requested/acknowledged
    assert assert_cancellation_ack_not_treated_as_confirmed(events) is not None

# --- Finding B: Sequence-Aware Ordering ---
def test_release_verified_after_dispatch_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, state=OperationState.DISPATCH_ATTEMPTED),
        _ev(EvidenceEventType.RELEASE_VERIFIED, state=OperationState.RELEASE_VERIFIED),
    ]
    assert assert_release_verified_before_dispatch(events) is not None

def test_release_consumed_after_dispatch_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED),
        _ev(EvidenceEventType.RELEASE_CONSUMED),
    ]
    assert assert_release_consumed_before_dispatch_attempted(events) is not None

def test_release_consumed_for_wrong_release_fails():
    events = [
        _ev(EvidenceEventType.RELEASE_CONSUMED, rel_id="rel_A"),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_B"),
    ]
    assert assert_release_consumed_before_dispatch_attempted(events) is not None

# --- Finding C: Retry Justification ---
def test_retry_after_unknown_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED),
        _ev(EvidenceEventType.OUTCOME_UNKNOWN, state=OperationState.OUTCOME_UNKNOWN),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED),
    ]
    assert assert_retries_justified(events) is not None

def test_retry_after_in_flight_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED),
        _ev(EvidenceEventType.IN_FLIGHT, state=OperationState.IN_FLIGHT),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED),
    ]
    assert assert_retries_justified(events) is not None

def test_retry_after_completion_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED),
        _ev(EvidenceEventType.COMPLETION_CONFIRMED, state=OperationState.COMPLETION_CONFIRMED),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED),
    ]
    assert assert_retries_justified(events) is not None

def test_retry_after_fenced_rejection_passes():
    """Must use a fresh release ID for a justified retry."""
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_1"),
        _ev(EvidenceEventType.DISPATCH_REJECTED, rel_id="rel_1", state=OperationState.DISPATCH_REJECTED, resp=ReceiverResponseClass.REJECTED_FENCED),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_2"),  # <--- Fresh release
    ]
    assert assert_retries_justified(events) is None