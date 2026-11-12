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
    for i, e in enumerate(events):
        if e.event_type is EvidenceEventType.DISPATCH_ATTEMPTED:
            found = False
            for j in range(i - 1, -1, -1):
                prior = events[j]
                if prior.event_type is EvidenceEventType.RELEASE_VERIFIED and \
                   prior.business_operation_id == e.business_operation_id and \
                   prior.release_id == e.release_id:
                    if e.dispatch_attempt_id and prior.dispatch_attempt_id:
                        if e.dispatch_attempt_id != prior.dispatch_attempt_id:
                            continue
                    found = True
                    break
            if not found:
                return f"dispatch_attempted for {e.release_id} without prior RELEASE_VERIFIED"
    return None


def assert_release_consumed_before_dispatch_attempted(events: Sequence[EvidenceEvent]) -> Optional[str]:
    """Sequence-aware check: RELEASE_CONSUMED must precede DISPATCH_ATTEMPTED."""
    for i, e in enumerate(events):
        if e.event_type is EvidenceEventType.DISPATCH_ATTEMPTED:
            found = False
            for j in range(i - 1, -1, -1):
                prior = events[j]
                if prior.event_type is EvidenceEventType.RELEASE_CONSUMED and \
                   prior.business_operation_id == e.business_operation_id and \
                   prior.release_id == e.release_id:
                    if e.dispatch_attempt_id and prior.dispatch_attempt_id:
                        if e.dispatch_attempt_id != prior.dispatch_attempt_id:
                            continue
                    found = True
                    break
            if not found:
                return f"dispatch_attempted for {e.release_id} without prior RELEASE_CONSUMED"
    return None


# --- cancellation / completion evidence ---------------------------------


def assert_cancellation_confirmed_has_terminal_evidence(events: Sequence[EvidenceEvent]) -> Optional[str]:
    """Ensure CANCELLATION_CONFIRMED is backed by explicit terminal status and a
    correlated prior REQUESTED/ACKNOWLEDGED event in the same operation."""
    for i, e in enumerate(events):
        if e.event_type is EvidenceEventType.CANCELLATION_CONFIRMED:
            if e.receiver_response_class != ReceiverResponseClass.STATUS_TERMINAL_CANCELLED:
                return f"CANCELLATION_CONFIRMED event {e.event_id} lacks STATUS_TERMINAL_CANCELLED"
            if not e.receiver_refund_reference:
                return f"CANCELLATION_CONFIRMED event {e.event_id} missing receiver_refund_reference"

            found_prior = False
            for j in range(i - 1, -1, -1):
                prior = events[j]
                # Skip events from other operations rather than failing.
                # The ledger may contain multiple operations; the assertion
                # only cares about events within THIS operation.
                if prior.business_operation_id != e.business_operation_id:
                    continue
                if prior.receiver_refund_reference == e.receiver_refund_reference and \
                   prior.event_type in (
                       EvidenceEventType.CANCELLATION_REQUESTED,
                       EvidenceEventType.CANCELLATION_ACKNOWLEDGED,
                   ):
                    if e.dispatch_attempt_id and prior.dispatch_attempt_id:
                        if e.dispatch_attempt_id != prior.dispatch_attempt_id:
                            continue
                    found_prior = True
                    break
            if not found_prior:
                return (
                    f"CANCELLATION_CONFIRMED for {e.receiver_refund_reference} "
                    f"lacks prior REQUESTED/ACKNOWLEDGED"
                )
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


_UNSAFE_RETRY_STATES = (
    "IN_FLIGHT",
    "OUTCOME_UNKNOWN",
    "COMPLETION_CONFIRMED",
    "CANCELLATION_ACKNOWLEDGED",
    "DISPATCH_ACCEPTED",
)


def assert_retries_justified(events: Sequence[EvidenceEvent]) -> Optional[str]:
    """Ensure no retry occurs after an unsafe/terminal state.

    Unsafe prior states:
      * IN_FLIGHT
      * OUTCOME_UNKNOWN
      * COMPLETION_CONFIRMED
      * CANCELLATION_ACKNOWLEDGED (a cancelled-but-not-confirmed attempt)
      * DISPATCH_ACCEPTED (the refund exists at the receiver)

    Reset points:
      * CANCELLATION_CONFIRMED (terminal cancellation resolves the pending state)

    A retry after DISPATCH_REJECTED must use a fresh release_id.
    """
    last_outcome: Optional[str] = None
    last_release_id: Optional[str] = None

    for e in events:
        if e.event_type is EvidenceEventType.DISPATCH_ATTEMPTED:
            if last_outcome in _UNSAFE_RETRY_STATES:
                return f"retry attempted after unsafe state {last_outcome}"
            if last_outcome == "DISPATCH_REJECTED":
                if e.release_id == last_release_id:
                    return (
                        f"retry attempted with same release {e.release_id} after rejection"
                    )

        # Reset point: CANCELLATION_CONFIRMED clears the unsafe state so a
        # genuinely new authorization can proceed.
        if e.event_type is EvidenceEventType.CANCELLATION_CONFIRMED:
            last_outcome = None
            last_release_id = None
            continue

        if e.event_type in (
            EvidenceEventType.DISPATCH_REJECTED,
            EvidenceEventType.OUTCOME_UNKNOWN,
            EvidenceEventType.IN_FLIGHT,
            EvidenceEventType.COMPLETION_CONFIRMED,
            EvidenceEventType.CANCELLATION_ACKNOWLEDGED,
            EvidenceEventType.DISPATCH_ACCEPTED,
        ):
            last_outcome = e.event_type.name
            last_release_id = e.release_id

    return None


# --- top-level aggregate ------------------------------------------------


ALL_ASSERTIONS = (
    ("no_guard_violations", lambda evs, ctx: assert_no_guard_violations(evs)),
    ("single_business_operation", assert_single_business_operation),
    ("ascending_timestamps", lambda evs, ctx: assert_events_have_ascending_timestamps(evs)),
    ("release_verified_before_dispatch", lambda evs, ctx: assert_release_verified_before_dispatch(evs)),
    ("release_consumed_before_dispatch_attempted", lambda evs, ctx: assert_release_consumed_before_dispatch_attempted(evs)),
    ("cancellation_confirmed_has_terminal_evidence", lambda evs, ctx: assert_cancellation_confirmed_has_terminal_evidence(evs)),
    ("completion_confirmed_has_terminal_completed", lambda evs, ctx: assert_completion_confirmed_has_terminal_completed(evs)),
    ("dispatch_rejected_has_valid_response", lambda evs, ctx: assert_dispatch_rejected_has_valid_response(evs)),
    ("retries_justified", lambda evs, ctx: assert_retries_justified(evs)),
)


__all__ = ["AssertionContext", "ALL_ASSERTIONS"]