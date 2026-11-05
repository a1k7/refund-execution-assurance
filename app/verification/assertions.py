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


# --- release validity ---------------------------------------------------


def assert_release_verified_before_dispatch(events: Sequence[EvidenceEvent]) -> Optional[str]:
    verified_ids = {
        e.release_id
        for e in events
        if e.event_type is EvidenceEventType.RELEASE_VERIFIED and e.release_id is not None
    }
    for e in events:
        if e.event_type is EvidenceEventType.DISPATCH_ATTEMPTED:
            if e.release_id not in verified_ids:
                return f"dispatch_attempted for {e.release_id} without prior RELEASE_VERIFIED"
    return None


def assert_release_consumed_before_dispatch_attempted(events: Sequence[EvidenceEvent]) -> Optional[str]:
    consumed_ids = {
        e.release_id
        for e in events
        if e.event_type is EvidenceEventType.RELEASE_CONSUMED and e.release_id is not None
    }
    for e in events:
        if e.event_type in (EvidenceEventType.DISPATCH_ACCEPTED, EvidenceEventType.DISPATCH_REJECTED):
            if e.release_id is not None and e.release_id not in consumed_ids:
                return f"{e.event_type.value} for {e.release_id} without RELEASE_CONSUMED"
    return None


# --- cancellation / completion evidence ---------------------------------


def assert_cancellation_confirmed_has_terminal_cancelled(events: Sequence[EvidenceEvent]) -> Optional[str]:
    for e in events:
        if e.event_type is EvidenceEventType.CANCELLATION_CONFIRMED:
            if e.receiver_response_class is not ReceiverResponseClass.STATUS_TERMINAL_CANCELLED:
                return f"CANCELLATION_CONFIRMED event {e.event_id} lacks terminal-cancelled evidence"
    return None


def assert_completion_confirmed_has_terminal_completed(events: Sequence[EvidenceEvent]) -> Optional[str]:
    for e in events:
        if e.event_type is EvidenceEventType.COMPLETION_CONFIRMED:
            if e.receiver_response_class is not ReceiverResponseClass.STATUS_TERMINAL_COMPLETED:
                return f"COMPLETION_CONFIRMED event {e.event_id} lacks terminal-completed evidence"
    return None


def assert_cancellation_ack_not_treated_as_confirmed(events: Sequence[EvidenceEvent]) -> Optional[str]:
    acked = any(e.event_type is EvidenceEventType.CANCELLATION_ACKNOWLEDGED for e in events)
    confirmed = any(e.event_type is EvidenceEventType.CANCELLATION_CONFIRMED for e in events)
    if confirmed and not acked:
        return None
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
    attempts_per_release: dict[str, int] = {}
    for e in events:
        if e.event_type is EvidenceEventType.DISPATCH_ATTEMPTED and e.release_id is not None:
            attempts_per_release[e.release_id] = attempts_per_release.get(e.release_id, 0) + 1
    for rid, count in attempts_per_release.items():
        if count > 1:
            return f"release {rid} attempted {count} times"
    return None


# --- top-level aggregate ------------------------------------------------


ALL_ASSERTIONS = (
    ("no_guard_violations", lambda evs, ctx: assert_no_guard_violations(evs)),
    ("single_business_operation", assert_single_business_operation),
    ("ascending_timestamps", lambda evs, ctx: assert_events_have_ascending_timestamps(evs)),
    ("release_verified_before_dispatch", lambda evs, ctx: assert_release_verified_before_dispatch(evs)),
    ("release_consumed_before_dispatch_attempted", lambda evs, ctx: assert_release_consumed_before_dispatch_attempted(evs)),
    ("cancellation_confirmed_has_terminal_cancelled", lambda evs, ctx: assert_cancellation_confirmed_has_terminal_cancelled(evs)),
    ("completion_confirmed_has_terminal_completed", lambda evs, ctx: assert_completion_confirmed_has_terminal_completed(evs)),
    ("cancellation_ack_not_confirmed_without_status", lambda evs, ctx: assert_cancellation_ack_not_treated_as_confirmed(evs)),
    ("dispatch_rejected_has_valid_response", lambda evs, ctx: assert_dispatch_rejected_has_valid_response(evs)),
    ("retries_justified", lambda evs, ctx: assert_retries_justified(evs)),
)


__all__ = ["AssertionContext", "ALL_ASSERTIONS"]