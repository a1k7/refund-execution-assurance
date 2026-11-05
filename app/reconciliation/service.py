"""Reconciliation service.

Purpose
-------
When an operation is in OUTCOME_UNKNOWN (dispatch inconclusive, cancellation
ack without terminal evidence, status query inconclusive), the only way to
legitimately resolve it is an independent status query to the receiver that
returns a terminal answer.

The service:
  * only acts on operations in OUTCOME_UNKNOWN (or with unknown terminal classification)
  * always records RECONCILIATION_ATTEMPTED before making any external call
  * when the receiver returns STATUS_TERMINAL_COMPLETED / _CANCELLED, emits the
    corresponding COMPLETION_CONFIRMED / CANCELLATION_CONFIRMED event so the
    independent verifier can pick it up as a terminal classification
  * otherwise records RECONCILIATION_INCONCLUSIVE and leaves the operation
    in OUTCOME_UNKNOWN
"""
from __future__ import annotations

from ..clock import Clock
from ..domain.enums import (
    EvidenceEventType,
    OperationState,
    ReceiverResponseClass,
    TerminalClassification,
)
from ..domain.errors import BadRequestError, NotFoundError
from ..domain.ids import new_correlation_id
from ..domain.state_machine import assert_transition, is_terminal
from ..evidence.ledger import EvidenceLedger
from ..evidence.schema import build_event
from ..persistence.db import Database
from ..persistence.repositories import OperationRepository
from ..receiver.protocol import ReceiverProtocol


class ReconciliationService:
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

    def reconcile(self, business_operation_id: str) -> OperationState:
        op = self.ops.get(business_operation_id)
        if op is None:
            raise NotFoundError(business_operation_id)
        if is_terminal(op.state):
            return op.state
        if op.state is not OperationState.OUTCOME_UNKNOWN:
            raise BadRequestError(
                f"operation is not OUTCOME_UNKNOWN (state={op.state.value})"
            )
        if op.receiver_refund_reference is None:
            raise BadRequestError(
                "operation has no receiver_refund_reference; cannot query status"
            )

        correlation_id = new_correlation_id()
        now = self.clock.now()

        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.RECONCILIATION_ATTEMPTED,
                evidence_source="reconciliation-service",
                timestamp=now,
                business_operation_id=business_operation_id,
                receiver_refund_reference=op.receiver_refund_reference,
                authority_state=op.authority_state,
                previous_state=op.state,
                new_state=op.state,
                correlation_id=correlation_id,
            )
        )

        response = self.receiver.query_status(
            business_operation_id=business_operation_id,
            receiver_refund_reference=op.receiver_refund_reference,
        )

        if response.response_class == ReceiverResponseClass.STATUS_TERMINAL_COMPLETED:
            assert_transition(op.state, OperationState.COMPLETION_CONFIRMED, reason="reconcile")
            self.ops.update_state(
                business_operation_id, OperationState.COMPLETION_CONFIRMED, self.clock.now()
            )
            self.ops.update_fields(
                business_operation_id,
                self.clock.now(),
                terminal_classification=TerminalClassification.COMPLETION_CONFIRMED,
                reconciliation_required=False,
                retry_eligible=False,
            )
            # Emit the terminal event so the independent verifier sees it.
            # RECONCILIATION_RESOLVED is carried in the payload for audit trail.
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.COMPLETION_CONFIRMED,
                    evidence_source="reconciliation-service",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    receiver_refund_reference=op.receiver_refund_reference,
                    receiver_response_class=response.response_class,
                    authority_state=op.authority_state,
                    previous_state=op.state,
                    new_state=OperationState.COMPLETION_CONFIRMED,
                    payload={
                        "resolved_via": "reconciliation",
                        "receiver_response": response.model_dump(mode="json"),
                    },
                    correlation_id=correlation_id,
                )
            )
            return OperationState.COMPLETION_CONFIRMED

        if response.response_class == ReceiverResponseClass.STATUS_TERMINAL_CANCELLED:
            assert_transition(op.state, OperationState.CANCELLATION_CONFIRMED, reason="reconcile")
            self.ops.update_state(
                business_operation_id, OperationState.CANCELLATION_CONFIRMED, self.clock.now()
            )
            self.ops.update_fields(
                business_operation_id,
                self.clock.now(),
                terminal_classification=TerminalClassification.CANCELLATION_CONFIRMED,
                reconciliation_required=False,
                retry_eligible=False,
            )
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.CANCELLATION_CONFIRMED,
                    evidence_source="reconciliation-service",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    receiver_refund_reference=op.receiver_refund_reference,
                    receiver_response_class=response.response_class,
                    authority_state=op.authority_state,
                    previous_state=op.state,
                    new_state=OperationState.CANCELLATION_CONFIRMED,
                    payload={
                        "resolved_via": "reconciliation",
                        "receiver_response": response.model_dump(mode="json"),
                    },
                    correlation_id=correlation_id,
                )
            )
            return OperationState.CANCELLATION_CONFIRMED

        # Anything else: still unresolved.
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.RECONCILIATION_INCONCLUSIVE,
                evidence_source="reconciliation-service",
                timestamp=self.clock.now(),
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
        self.ops.update_fields(
            business_operation_id,
            self.clock.now(),
            reconciliation_required=True,
            retry_eligible=False,
        )
        return OperationState.OUTCOME_UNKNOWN


__all__ = ["ReconciliationService"]