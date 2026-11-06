"""Named, side-effect-free assertions used by the verifier.

Each assertion takes ``(events, ctx)`` and returns ``None`` on success or a
short failure string. Assertions never raise and never touch the receiver,
DB, or workflow engine. This makes them trivially composable and audit-friendly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..domain.enums import EvidenceEventType, OperationState, ReceiverResponseClass
from ..domain.models import EvidenceEvent


@dataclass
class AssertionContext:
    business_operation_id: str
    events: List[EvidenceEvent]


# --- basic structural checks --------------------------------------------


def assert_no_guard_violations(events: Sequence[EvidenceEvent]) -> Optional[str]:
    bad = [e for e in events if e.event_type is EvidenceEventType.GUARD_VIOLATED]
    if bad:
        return f"{len(bad)} guard violation(s) recorded"
    return None


def assert_single_business_operation(events: Sequence[EvidenceEvent], ctx: AssertionContext) -> Optional[str]:
    for e in events:
        if e.business_operation_id is not None and e.business_operation_id != ctx.business_operation_id:
            return f"event {e.event_id} has foreign business_operation_id={e.business_operation_id}"
    return None


def assert_events_have_ascending_timestamps(events: Sequence[EvidenceEvent]) -> Optional[str]:
    last = None
    for e in events:
        if last is not None and e.timestamp < last:
            return f"event {e.event_id} timestamp out of order"
        last = e.timestamp
    return None


# --- release validity & ordering ----------------------------------------


def assert_release_verified_before_dispatch(events: Sequence[EvidenceEvent]) -> Optional[str]:
    """Sequence-aware check: RELEASE_VERIFIED must precede DISPATCH_ATTEMPTED."""
    verified_at = {}
    for i, e in enumerate(events):
        if e.event_type is EvidenceEventType.RELEASE_VERIFIED and e.release_id:
            verified_at[e.release_id] = i

    for i, e in enumerate(events):
        if e.event_type is EvidenceEventType.DISPATCH_ATTEMPTED:
            if not e.release_id:
                return f"dispatch_attempted {e.event_id} missing release_id"
            if e.release_id not in verified_at:
                return f"dispatch_attempted for {e.release_id} without prior RELEASE_VERIFIED"
            if verified_at[e.release_id] >= i:
                return f"RELEASE_VERIFIED for {e.release_id} occurred after DISPATCH_ATTEMPTED"
    return None


def assert_release_consumed_before_dispatch_attempted(events: Sequence[EvidenceEvent]) -> Optional[str]:
    """Sequence-aware check: RELEASE_CONSUMED must precede DISPATCH_ATTEMPTED."""
    consumed_at = {}
    for i, e in enumerate(events):
        if e.event_type is EvidenceEventType.RELEASE_CONSUMED and e.release_id:
            consumed_at[e.release_id] = i

    for i, e in enumerate(events):
        if e.event_type is EvidenceEventType.DISPATCH_ATTEMPTED:
            if not e.release_id:
                return f"dispatch_attempted {e.event_id} missing release_id"
            if e.release_id not in consumed_at:
                return f"dispatch_attempted for {e.release_id} without prior RELEASE_CONSUMED"
            if consumed_at[e.release_id] >= i:
                return f"RELEASE_CONSUMED for {e.release_id} occurred after DISPATCH_ATTEMPTED"
    return None


# --- cancellation / completion evidence ---------------------------------


def assert_cancellation_ack_not_treated_as_confirmed(events: Sequence[EvidenceEvent]) -> Optional[str]:
    """Ensure CANCELLATION_CONFIRMED is backed by explicit terminal status, not just ACK."""
    for e in events:
        if e.event_type is EvidenceEventType.CANCELLATION_CONFIRMED:
            # Must have explicitly recorded the terminal status on this very event.
            if e.receiver_response_class != ReceiverResponseClass.STATUS_TERMINAL_CANCELLED:
                return f"CANCELLATION_CONFIRMED event {e.event_id} lacks STATUS_TERMINAL_CANCELLED"
            
            # Must have a valid receiver reference to prevent cross-operation confusion.
            if not e.receiver_refund_reference:
                return f"CANCELLATION_CONFIRMED event {e.event_id} missing receiver_refund_reference"
            
            # Must be preceded by a REQUESTED or ACKNOWLEDGED event for the SAME reference.
            prior_refs = {
                ev.receiver_refund_reference for ev in events
                if ev.event_type in (EvidenceEventType.CANCELLATION_REQUESTED, EvidenceEventType.CANCELLATION_ACKNOWLEDGED)
                and ev.receiver_refund_reference is not None
            }
            if e.receiver_refund_reference not in prior_refs:
                return f"CANCELLATION_CONFIRMED for {e.receiver_refund_reference} lacks prior REQUESTED/ACKNOWLEDGED"
    return None


def assert_completion_confirmed_has_terminal_completed(events: Sequence[EvidenceEvent]) -> Optional[str]:
    for e in events:
        if e.event_type is EvidenceEventType.COMPLETION_CONFIRMED:
            if e.receiver_response_class is not ReceiverResponseClass.STATUS_TERMINAL_COMPLETED:
                return f"COMPLETION_CONFIRMED event {e.event_id} lacks terminal-completed evidence"
    return None


def assert_dispatch_rejected_has_valid_response(events: Sequence[EvidenceEvent]) -> Optional[str]:
    """A DISPATCH_REJECTED event must carry a REJECTED_* receiver response class."""
    for e in events:
        if e.event_type is EvidenceEventType.DISPATCH_REJECTED:
            if e.receiver_response_class is None:
                return f"DISPATCH_REJECTED event {e.event_id} missing receiver_response_class"
            if not e.receiver_response_class.name.startswith("REJECTED_"):
                return f"DISPATCH_REJECTED event {e.event_id} has invalid response {e.receiver_response_class.name}"
    return None


# --- retry history ------------------------------------------------------


def assert_retries_justified(events: Sequence[EvidenceEvent]) -> Optional[str]:
    """Ensure no retry occurs after an unsafe/terminal state."""
    last_attempt_state = None
    for e in events:
        if e.event_type is EvidenceEventType.DISPATCH_ATTEMPTED:
            if last_attempt_state in (
                EvidenceEventType.OUTCOME_UNKNOWN,
                EvidenceEventType.IN_FLIGHT,
                EvidenceEventType.COMPLETION_CONFIRMED,
                EvidenceEventType.CANCELLATION_CONFIRMED,
            ):
                return f"retry attempted after unsafe state {last_attempt_state.name}"
        
        if e.event_type is EvidenceEventType.DISPATCH_REJECTED:
            last_attempt_state = e.event_type
        elif e.event_type is EvidenceEventType.ADMISSIBILITY_DENIED:
            last_attempt_state = e.event_type
        elif e.event_type in (
            EvidenceEventType.OUTCOME_UNKNOWN,
            EvidenceEventType.IN_FLIGHT,
            EvidenceEventType.COMPLETION_CONFIRMED,
            EvidenceEventType.CANCELLATION_CONFIRMED,
        ):
            last_attempt_state = e.event_type

    return None


# --- top-level aggregate ------------------------------------------------


ALL_ASSERTIONS = (
    ("no_guard_violations", lambda evs, ctx: assert_no_guard_violations(evs)),
    ("single_business_operation", assert_single_business_operation),
    ("ascending_timestamps", lambda evs, ctx: assert_events_have_ascending_timestamps(evs)),
    ("release_verified_before_dispatch", lambda evs, ctx: assert_release_verified_before_dispatch(evs)),
    ("release_consumed_before_dispatch_attempted", lambda evs, ctx: assert_release_consumed_before_dispatch_attempted(evs)),
    ("cancellation_confirmed_has_terminal_cancelled", lambda evs, ctx: assert_cancellation_ack_not_treated_as_confirmed(evs)),
    ("completion_confirmed_has_terminal_completed", lambda evs, ctx: assert_completion_confirmed_has_terminal_completed(evs)),
    ("dispatch_rejected_has_valid_response", lambda evs, ctx: assert_dispatch_rejected_has_valid_response(evs)),
    ("retries_justified", lambda evs, ctx: assert_retries_justified(evs)),
)


__all__ = ["AssertionContext", "ALL_ASSERTIONS"]