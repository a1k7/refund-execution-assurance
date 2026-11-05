"""Reconciliation endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..domain.errors import BadRequestError, NotFoundError
from .deps import AppState, get_app_state
from .schemas import ReconciliationRequest, ReconciliationResponse

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post("/reconcile", response_model=ReconciliationResponse)
def reconcile(
    body: ReconciliationRequest, state: AppState = Depends(get_app_state)
) -> ReconciliationResponse:
    try:
        new_state = state.reconciliation.reconcile(body.business_operation_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReconciliationResponse(
        business_operation_id=body.business_operation_id, state=new_state.value
    )