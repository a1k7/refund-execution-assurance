"""Third-pass regression tests for verifier assertions (PR #2 Final)."""
import pytest
from datetime import datetime, timezone
from app.domain.enums import (
    EvidenceEventType,
    OperationState,
    ReceiverResponseClass,
)
from app.domain.models import EvidenceEvent
from app.verification.assertions import (
    assert_retries_justified,
    ALL_ASSERTIONS,
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

# --- Finding A: Unsafe retry after DISPATCH_ACCEPTED ---
def test_retry_after_accepted_dispatch_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_1", att_id="att_1"),
        _ev(EvidenceEventType.DISPATCH_ACCEPTED, rel_id="rel_1", att_id="att_1", state=OperationState.DISPATCH_ACCEPTED),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_2", att_id="att_2"),
    ]
    assert assert_retries_justified(events) is not None

# --- Finding B: Legitimate retry after confirmed cancellation ---
def test_retry_after_confirmed_cancellation_and_new_authorization_passes():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_1", att_id="att_1"),
        _ev(EvidenceEventType.DISPATCH_ACCEPTED, rel_id="rel_1", att_id="att_1", state=OperationState.DISPATCH_ACCEPTED),
        _ev(EvidenceEventType.CANCELLATION_REQUESTED, rel_id="rel_1", att_id="att_1"),
        _ev(EvidenceEventType.CANCELLATION_ACKNOWLEDGED, rel_id="rel_1", att_id="att_1", state=OperationState.CANCELLATION_ACKNOWLEDGED),
        _ev(EvidenceEventType.CANCELLATION_CONFIRMED, rel_id="rel_1", att_id="att_1", state=OperationState.CANCELLATION_CONFIRMED, resp=ReceiverResponseClass.STATUS_TERMINAL_CANCELLED),
        _ev(EvidenceEventType.RELEASE_VERIFIED, rel_id="rel_2"),
        _ev(EvidenceEventType.RELEASE_CONSUMED, rel_id="rel_2"),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_2", att_id="att_2"),
    ]
    assert assert_retries_justified(events) is None

def test_retry_after_ack_without_confirmation_fails():
    events = [
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_1", att_id="att_1"),
        _ev(EvidenceEventType.DISPATCH_ACCEPTED, rel_id="rel_1", att_id="att_1", state=OperationState.DISPATCH_ACCEPTED),
        _ev(EvidenceEventType.CANCELLATION_REQUESTED, rel_id="rel_1", att_id="att_1"),
        _ev(EvidenceEventType.CANCELLATION_ACKNOWLEDGED, rel_id="rel_1", att_id="att_1", state=OperationState.CANCELLATION_ACKNOWLEDGED),
        _ev(EvidenceEventType.DISPATCH_ATTEMPTED, rel_id="rel_2", att_id="att_2"),
    ]
    assert assert_retries_justified(events) is not None

# --- Cross-operation regression (Pavan's finding) ---
def test_cancellation_confirmed_ignores_foreign_operation_events():
    """A CANCELLATION_CONFIRMED must pass even if another operation's events
    appear earlier in the list. This is a regression for the cross-operation
    false-failure bug."""
    events = [
        _ev(EvidenceEventType.OPERATION_CREATED, op_id="bo_other"),
        _ev(EvidenceEventType.CANCELLATION_REQUESTED, op_id="bo_1", ref="ref_1"),
        _ev(EvidenceEventType.CANCELLATION_ACKNOWLEDGED, op_id="bo_1", ref="ref_1"),
        _ev(EvidenceEventType.CANCELLATION_CONFIRMED, op_id="bo_1", ref="ref_1",
            state=OperationState.CANCELLATION_CONFIRMED,
            resp=ReceiverResponseClass.STATUS_TERMINAL_CANCELLED),
    ]
    from app.verification.assertions import assert_cancellation_confirmed_has_terminal_evidence
    assert assert_cancellation_confirmed_has_terminal_evidence(events) is None

# --- Finding C: Naming Correction ---
def test_assertion_naming_is_corrected():
    names = [name for name, _ in ALL_ASSERTIONS]
    assert "cancellation_confirmed_has_terminal_evidence" in names
    assert "cancellation_confirmed_has_terminal_cancelled" not in names