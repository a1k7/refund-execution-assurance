"""Cancellation service. Strictly separates REQUESTED, ACKNOWLEDGED, CONFIRMED.

Rules enforced here:
  * Cancellation mode is only applied when the receiver declares BOTH
    ``supports_cancellation`` and ``supports_status_query``. Without status
    query there is no way to independently confirm cancellation, and we
    refuse to pretend otherwise.
  * ``request_cancellation`` writes CANCELLATION_REQUESTED, then calls
    ``receiver.cancel``. A CANCEL_ACK transitions to CANCELLATION_ACKNOWLEDGED
    but does NOT confirm cancellation.
  * ``confirm_from_status`` (or the post-ack status check inside request)
    issues a ``query_status`` call. Only a STATUS_TERMINAL_CANCELLED response
    promotes to CANCELLATION_CONFIRMED. STATUS_TERMINAL_COMPLETED promotes to
    COMPLETION_CONFIRMED. Anything else stays in the current state or moves
    to OUTCOME_UNKNOWN and sets reconciliation_required=True.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..clock import Clock
from ..domain.enums import (
    EvidenceEventType,
    OperationState,
    ReceiverResponseClass,
    TerminalClassification,
)
from ..domain.errors import (
    BadRequestError,
    NotFoundError,
    ReceiverCapabilityError,
)
from ..domain.ids import new_correlation_id
from ..domain.models import ReceiverResponse
from ..domain.state_machine import assert_transition, is_terminal
from ..evidence.ledger import EvidenceLedger
from ..evidence.schema import build_event
from ..persistence.db import Database
from ..persistence.repositories import OperationRepository
from ..receiver.protocol import ReceiverProtocol


class CancellationService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        receiver: ReceiverProtocol,
    ) -> None:
        self.db = db
        self.clock = clock
        self.receiver = receiver
        self.ops = OperationRepository(db)
        self.ledger = EvidenceLedger(db)

    def validate_receiver_capabilities(self) -> None:
        caps = self.receiver.capabilities
        if not caps.supports_cancellation:
            raise ReceiverCapabilityError(
                "receiver does not support cancellation; REQUEST_CANCELLATION_OF_IN_FLIGHT is unsafe"
            )
        if not caps.supports_status_query:
            raise ReceiverCapabilityError(
                "receiver does not support status query; cancellation cannot be independently confirmed"
            )

    # ------------------------------------------------------------------

    def request_cancellation(self, business_operation_id: str) -> OperationState:
        self.validate_receiver_capabilities()

        op = self.ops.get(business_operation_id)
        if op is None:
            raise NotFoundError(business_operation_id)
        if is_terminal(op.state):
            raise BadRequestError(f"operation already terminal: {op.state.value}")
        if op.state not in (
            OperationState.IN_FLIGHT,
            OperationState.DISPATCH_ACCEPTED,
        ):
            raise BadRequestError(
                f"cannot request cancellation from state {op.state.value}"
            )
        if op.receiver_refund_reference is None:
            raise BadRequestError("operation has no receiver_refund_reference")

        correlation_id = new_correlation_id()
        now = self.clock.now()

        assert_transition(op.state, OperationState.CANCELLATION_REQUESTED, reason="cancel-request")
        self.ops.update_state(business_operation_id, OperationState.CANCELLATION_REQUESTED, now)
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.CANCELLATION_REQUESTED,
                evidence_source="cancellation-service",
                timestamp=now,
                business_operation_id=business_operation_id,
                receiver_refund_reference=op.receiver_refund_reference,
                authority_state=op.authority_state,
                previous_state=op.state,
                new_state=OperationState.CANCELLATION_REQUESTED,
                correlation_id=correlation_id,
            )
        )

        response = self.receiver.cancel(
            business_operation_id=business_operation_id,
            receiver_refund_reference=op.receiver_refund_reference,
        )

        if response.response_class == ReceiverResponseClass.CANCEL_ACK:
            assert_transition(
                OperationState.CANCELLATION_REQUESTED,
                OperationState.CANCELLATION_ACKNOWLEDGED,
                reason="cancel-ack",
            )
            self.ops.update_state(
                business_operation_id, OperationState.CANCELLATION_ACKNOWLEDGED, self.clock.now()
            )
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.CANCELLATION_ACKNOWLEDGED,
                    evidence_source="cancellation-service",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    receiver_refund_reference=op.receiver_refund_reference,
                    receiver_response_class=response.response_class,
                    authority_state=op.authority_state,
                    previous_state=OperationState.CANCELLATION_REQUESTED,
                    new_state=OperationState.CANCELLATION_ACKNOWLEDGED,
                    payload=response.model_dump(mode="json"),
                    correlation_id=correlation_id,
                )
            )
            # Acknowledgment alone is not confirmation. Try a status query to see if
            # the receiver has already reached a terminal state (may be PENDING).
            return self._attempt_terminal_from_status(
                business_operation_id=business_operation_id,
                correlation_id=correlation_id,
                reason="post_ack_status_check",
            )

        if response.response_class == ReceiverResponseClass.CANCEL_REJECTED:
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.CANCELLATION_REJECTED,
                    evidence_source="cancellation-service",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    receiver_refund_reference=op.receiver_refund_reference,
                    receiver_response_class=response.response_class,
                    authority_state=op.authority_state,
                    previous_state=OperationState.CANCELLATION_REQUESTED,
                    new_state=OperationState.CANCELLATION_REQUESTED,
                    payload=response.model_dump(mode="json"),
                    correlation_id=correlation_id,
                )
            )
            # Receiver refused. The refund may still be in flight or already
            # terminal. Query status to try to establish a terminal fact.
            return self._attempt_terminal_from_status(
                business_operation_id=business_operation_id,
                correlation_id=correlation_id,
                reason="post_reject_status_check",
            )

        # Unknown / transport error class from cancel.
        return self._mark_outcome_unknown(
            business_operation_id=business_operation_id,
            correlation_id=correlation_id,
            response=response,
            reason="cancel_inconclusive",
        )

    # ------------------------------------------------------------------

    def confirm_from_status(self, business_operation_id: str) -> OperationState:
        """Explicitly query receiver status and attempt to establish terminal truth."""
        self.validate_receiver_capabilities()

        op = self.ops.get(business_operation_id)
        if op is None:
            raise NotFoundError(business_operation_id)
        if is_terminal(op.state):
            return op.state
        if op.receiver_refund_reference is None:
            raise BadRequestError("operation has no receiver_refund_reference")

        return self._attempt_terminal_from_status(
            business_operation_id=business_operation_id,
            correlation_id=new_correlation_id(),
            reason="explicit_status_query",
        )

    # ------------------------------------------------------------------

    def _attempt_terminal_from_status(
        self,
        *,
        business_operation_id: str,
        correlation_id: str,
        reason: str,
    ) -> OperationState:
        op = self.ops.get(business_operation_id)
        assert op is not None
        assert op.receiver_refund_reference is not None

        response = self.receiver.query_status(
            business_operation_id=business_operation_id,
            receiver_refund_reference=op.receiver_refund_reference,
        )
        now = self.clock.now()

        if response.response_class == ReceiverResponseClass.STATUS_TERMINAL_CANCELLED:
            assert_transition(op.state, OperationState.CANCELLATION_CONFIRMED, reason=reason)
            self.ops.update_state(business_operation_id, OperationState.CANCELLATION_CONFIRMED, now)
            self.ops.update_fields(
                business_operation_id,
                now,
                terminal_classification=TerminalClassification.CANCELLATION_CONFIRMED,
                reconciliation_required=False,
                retry_eligible=False,
            )
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.CANCELLATION_CONFIRMED,
                    evidence_source="cancellation-service",
                    timestamp=now,
                    business_operation_id=business_operation_id,
                    receiver_refund_reference=op.receiver_refund_reference,
                    receiver_response_class=response.response_class,
                    authority_state=op.authority_state,
                    previous_state=op.state,
                    new_state=OperationState.CANCELLATION_CONFIRMED,
                    payload=response.model_dump(mode="json"),
                    correlation_id=correlation_id,
                )
            )
            return OperationState.CANCELLATION_CONFIRMED

        if response.response_class == ReceiverResponseClass.STATUS_TERMINAL_COMPLETED:
            assert_transition(op.state, OperationState.COMPLETION_CONFIRMED, reason=reason)
            self.ops.update_state(business_operation_id, OperationState.COMPLETION_CONFIRMED, now)
            self.ops.update_fields(
                business_operation_id,
                now,
                terminal_classification=TerminalClassification.COMPLETION_CONFIRMED,
                reconciliation_required=False,
                retry_eligible=False,
            )
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.COMPLETION_CONFIRMED,
                    evidence_source="cancellation-service",
                    timestamp=now,
                    business_operation_id=business_operation_id,
                    receiver_refund_reference=op.receiver_refund_reference,
                    receiver_response_class=response.response_class,
                    authority_state=op.authority_state,
                    previous_state=op.state,
                    new_state=OperationState.COMPLETION_CONFIRMED,
                    payload=response.model_dump(mode="json"),
                    correlation_id=correlation_id,
                )
            )
            return OperationState.COMPLETION_CONFIRMED

        # PENDING, STATUS_UNKNOWN, TRANSPORT_ERROR, INCONCLUSIVE, etc.
        return self._mark_outcome_unknown(
            business_operation_id=business_operation_id,
            correlation_id=correlation_id,
            response=response,
            reason=reason,
        )

    def _mark_outcome_unknown(
        self,
        *,
        business_operation_id: str,
        correlation_id: str,
        response: ReceiverResponse,
        reason: str,
    ) -> OperationState:
        op = self.ops.get(business_operation_id)
        assert op is not None
        now = self.clock.now()

        if op.state is not OperationState.OUTCOME_UNKNOWN:
            assert_transition(op.state, OperationState.OUTCOME_UNKNOWN, reason=reason)
            self.ops.update_state(business_operation_id, OperationState.OUTCOME_UNKNOWN, now)
        self.ops.update_fields(
            business_operation_id,
            now,
            terminal_classification=TerminalClassification.OUTCOME_UNKNOWN,
            reconciliation_required=True,
            retry_eligible=False,
        )
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.OUTCOME_UNKNOWN,
                evidence_source="cancellation-service",
                timestamp=now,
                business_operation_id=business_operation_id,
                receiver_refund_reference=op.receiver_refund_reference,
                receiver_response_class=response.response_class,
                authority_state=op.authority_state,
                previous_state=op.state,
                new_state=OperationState.OUTCOME_UNKNOWN,
                payload=response.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
        )
        return OperationState.OUTCOME_UNKNOWN


__all__ = ["CancellationService"]