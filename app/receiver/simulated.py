"""Simulated external refund receiver.

HONESTY:
  * This is a local simulation. It is NOT a live payment integration.
  * The internal ``_refunds`` and ``_fencing`` state is the receiver's
    authoritative ground truth. It is NEVER exposed by any public method,
    never stored in evidence, and never read by the verifier.
  * An independent test helper under ``tests/`` may read this state for
    assertions. It is not importable from ``app/``.

Behaviour controls
------------------
``SimulatedReceiverBehaviour`` lets a test deterministically configure:
  * whether cancellation and status queries are supported
  * whether acceptance immediately completes the refund internally
  * what ``accept`` / ``cancel`` / ``query_status`` return (overrides)
  * whether cancellation transitions the refund to a terminal state

Every method is thread-safe. There are no timers, delays, or random choices.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from ..clock import Clock
from ..domain.enums import ReceiverResponseClass
from ..domain.ids import new_receiver_refund_reference
from ..domain.models import ReceiverResponse
from .fencing import FencingRegistry
from .protocol import ReceiverCapabilities


class _RefundState(str, Enum):
    ACCEPTED = "ACCEPTED"
    IN_FLIGHT = "IN_FLIGHT"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass
class _RefundRecord:
    receiver_refund_reference: str
    business_operation_id: str
    release_id: str
    release_epoch: int
    idempotency_key: str
    amount: int
    currency: str
    state: _RefundState
    created_at: datetime
    updated_at: datetime
    cancellation_requested_at: Optional[datetime] = None


@dataclass
class SimulatedReceiverBehaviour:
    supports_cancellation: bool = True
    supports_status_query: bool = True
    complete_on_accept: bool = False
    cancel_transitions_to_terminal: bool = True
    accept_class_override: Optional[ReceiverResponseClass] = None
    cancel_class_override: Optional[ReceiverResponseClass] = None
    status_class_override: Optional[ReceiverResponseClass] = None
    reject_unknown_refunds: bool = True


# Response classes that unambiguously indicate "no refund was created".
_NO_REFUND_CLASSES = frozenset({
    ReceiverResponseClass.REJECTED_FENCED,
    ReceiverResponseClass.REJECTED_DUPLICATE,
    ReceiverResponseClass.REJECTED_INVALID,
})


class SimulatedRefundReceiver:
    """See module docstring for honesty and behaviour notes."""

    def __init__(
        self,
        clock: Clock,
        behaviour: Optional[SimulatedReceiverBehaviour] = None,
    ) -> None:
        self._clock = clock
        self._behaviour = behaviour or SimulatedReceiverBehaviour()
        self._lock = threading.Lock()
        self._refunds: Dict[str, _RefundRecord] = {}
        self._by_idempotency: Dict[str, str] = {}
        self._fencing = FencingRegistry()

    @property
    def capabilities(self) -> ReceiverCapabilities:
        return ReceiverCapabilities(
            supports_cancellation=self._behaviour.supports_cancellation,
            supports_status_query=self._behaviour.supports_status_query,
        )

    def accept(
        self,
        *,
        business_operation_id: str,
        release_id: str,
        release_epoch: int,
        idempotency_key: str,
        amount: int,
        currency: str,
    ) -> ReceiverResponse:
        with self._lock:
            if self._fencing.is_stale(business_operation_id, release_epoch):
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.REJECTED_FENCED,
                    detail=f"stale_epoch:{release_epoch}<{self._fencing.current(business_operation_id)}",
                )

            existing_ref = self._by_idempotency.get(idempotency_key)
            if existing_ref is not None:
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.ACCEPTED,
                    receiver_refund_reference=existing_ref,
                    detail="idempotent_replay",
                )

            # Behaviour override. Rationale for refund recording:
            # * A REJECTED_* class means the receiver definitely did not create
            #   a refund → do NOT record one.
            # * INCONCLUSIVE / ACCEPTED means the receiver has (or may have)
            #   created the refund; its terminal state is unknown to the caller.
            #   Record it so later reconciliation has a reference to query.
            # * Any other class follows the same rule (record unless rejected).
            if self._behaviour.accept_class_override is not None:
                override = self._behaviour.accept_class_override
                if override in _NO_REFUND_CLASSES:
                    return ReceiverResponse(
                        response_class=override,
                        detail="accept_class_override_rejected",
                    )
                terminal_state = (
                    _RefundState.COMPLETED
                    if self._behaviour.complete_on_accept
                    else _RefundState.IN_FLIGHT
                )
                ref = self._record_refund(
                    business_operation_id=business_operation_id,
                    release_id=release_id,
                    release_epoch=release_epoch,
                    idempotency_key=idempotency_key,
                    amount=amount,
                    currency=currency,
                    terminal_state=terminal_state,
                )
                return ReceiverResponse(
                    response_class=override,
                    receiver_refund_reference=ref,
                    detail="accept_class_override_recorded",
                )

            # Normal path.
            terminal_state = (
                _RefundState.COMPLETED
                if self._behaviour.complete_on_accept
                else _RefundState.IN_FLIGHT
            )
            ref = self._record_refund(
                business_operation_id=business_operation_id,
                release_id=release_id,
                release_epoch=release_epoch,
                idempotency_key=idempotency_key,
                amount=amount,
                currency=currency,
                terminal_state=terminal_state,
            )
            return ReceiverResponse(
                response_class=ReceiverResponseClass.ACCEPTED,
                receiver_refund_reference=ref,
                detail="accepted",
            )

    def cancel(
        self,
        *,
        business_operation_id: str,
        receiver_refund_reference: str,
    ) -> ReceiverResponse:
        with self._lock:
            if not self._behaviour.supports_cancellation:
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.CANCEL_REJECTED,
                    detail="cancellation_not_supported",
                )
            record = self._refunds.get(receiver_refund_reference)
            if record is None:
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.CANCEL_REJECTED,
                    detail="unknown_refund_reference",
                )
            record.cancellation_requested_at = self._clock.now()
            if self._behaviour.cancel_class_override is not None:
                return ReceiverResponse(
                    response_class=self._behaviour.cancel_class_override,
                    receiver_refund_reference=receiver_refund_reference,
                    detail="cancel_class_override",
                )
            if record.state == _RefundState.COMPLETED:
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.CANCEL_REJECTED,
                    receiver_refund_reference=receiver_refund_reference,
                    detail="already_completed",
                )
            if record.state == _RefundState.CANCELLED:
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.CANCEL_ACK,
                    receiver_refund_reference=receiver_refund_reference,
                    detail="already_cancelled",
                )
            if record.state == _RefundState.IN_FLIGHT:
                if self._behaviour.cancel_transitions_to_terminal:
                    record.state = _RefundState.CANCELLED
                    record.updated_at = self._clock.now()
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.CANCEL_ACK,
                    receiver_refund_reference=receiver_refund_reference,
                    detail="cancel_acknowledged",
                )
            return ReceiverResponse(
                response_class=ReceiverResponseClass.CANCEL_REJECTED,
                receiver_refund_reference=receiver_refund_reference,
                detail=f"unexpected_state:{record.state.value}",
            )

    def query_status(
        self,
        *,
        business_operation_id: str,
        receiver_refund_reference: str,
    ) -> ReceiverResponse:
        with self._lock:
            if not self._behaviour.supports_status_query:
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.STATUS_UNKNOWN,
                    detail="status_not_supported",
                )
            if self._behaviour.status_class_override is not None:
                return ReceiverResponse(
                    response_class=self._behaviour.status_class_override,
                    receiver_refund_reference=receiver_refund_reference,
                    detail="status_class_override",
                )
            record = self._refunds.get(receiver_refund_reference)
            if record is None:
                return ReceiverResponse(
                    response_class=(
                        ReceiverResponseClass.STATUS_UNKNOWN
                        if self._behaviour.reject_unknown_refunds
                        else ReceiverResponseClass.STATUS_PENDING
                    ),
                    detail="unknown_refund_reference",
                )
            if record.state == _RefundState.COMPLETED:
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.STATUS_TERMINAL_COMPLETED,
                    receiver_refund_reference=receiver_refund_reference,
                )
            if record.state == _RefundState.CANCELLED:
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.STATUS_TERMINAL_CANCELLED,
                    receiver_refund_reference=receiver_refund_reference,
                )
            if record.state in (_RefundState.ACCEPTED, _RefundState.IN_FLIGHT):
                return ReceiverResponse(
                    response_class=ReceiverResponseClass.STATUS_PENDING,
                    receiver_refund_reference=receiver_refund_reference,
                )
            return ReceiverResponse(
                response_class=ReceiverResponseClass.STATUS_UNKNOWN,
                detail=f"unexpected_state:{record.state.value}",
            )

    def update_fencing_state(self, business_operation_id: str, current_epoch: int) -> None:
        self._fencing.update(business_operation_id, current_epoch)

    def reset(self) -> None:
        with self._lock:
            self._refunds.clear()
            self._by_idempotency.clear()
            self._fencing.reset()

    def set_behaviour(self, behaviour: SimulatedReceiverBehaviour) -> None:
        with self._lock:
            self._behaviour = behaviour

    def _record_refund(
        self,
        *,
        business_operation_id: str,
        release_id: str,
        release_epoch: int,
        idempotency_key: str,
        amount: int,
        currency: str,
        terminal_state: _RefundState,
    ) -> str:
        now = self._clock.now()
        ref = new_receiver_refund_reference()
        self._refunds[ref] = _RefundRecord(
            receiver_refund_reference=ref,
            business_operation_id=business_operation_id,
            release_id=release_id,
            release_epoch=release_epoch,
            idempotency_key=idempotency_key,
            amount=amount,
            currency=currency,
            state=terminal_state,
            created_at=now,
            updated_at=now,
        )
        self._by_idempotency[idempotency_key] = ref
        return ref