"""Read-only operation views."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..persistence.repositories import DispatchAttemptRepository, OperationRepository
from .deps import AppState, get_app_state
from .schemas import AttemptView, OperationHistoryResponse, OperationView

router = APIRouter(prefix="/operations", tags=["operations"])


def _op_view(op) -> OperationView:
    return OperationView(
        business_operation_id=op.business_operation_id,
        state=op.state.value,
        authority_state=op.authority_state.value,
        in_flight_policy=op.in_flight_policy.value,
        terminal_classification=op.terminal_classification.value if op.terminal_classification else None,
        retry_eligible=op.retry_eligible,
        reconciliation_required=op.reconciliation_required,
        receiver_refund_reference=op.receiver_refund_reference,
        current_epoch=op.current_epoch,
    )


@router.get("", response_model=list[OperationView])
def list_operations(state: AppState = Depends(get_app_state)) -> list[OperationView]:
    return [_op_view(op) for op in OperationRepository(state.db).list_all()]


@router.get("/{business_operation_id}", response_model=OperationHistoryResponse)
def get_operation(
    business_operation_id: str, state: AppState = Depends(get_app_state)
) -> OperationHistoryResponse:
    ops = OperationRepository(state.db)
    op = ops.get(business_operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    attempts = DispatchAttemptRepository(state.db).list_for_operation(business_operation_id)
    decision = state.retry_policy.evaluate(business_operation_id)
    return OperationHistoryResponse(
        operation=_op_view(op),
        attempts=[
            AttemptView(
                dispatch_attempt_id=a.dispatch_attempt_id,
                release_id=a.release_id,
                release_epoch=a.release_epoch,
                idempotency_key=a.idempotency_key,
                state=a.state.value,
                response_class=a.response_class.value if a.response_class else None,
                receiver_refund_reference=a.receiver_refund_reference,
            )
            for a in attempts
        ],
        retry_decision={"decision": decision.decision.value, "reason": decision.reason},
    )