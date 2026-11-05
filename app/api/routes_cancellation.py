"""Cancellation endpoints. Strictly separates REQUESTED, ACKNOWLEDGED, CONFIRMED."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..domain.errors import BadRequestError, NotFoundError, ReceiverCapabilityError
from .deps import AppState, get_app_state
from .schemas import CancellationRequest, CancellationResponse

router = APIRouter(prefix="/cancellation", tags=["cancellation"])


@router.post("/request", response_model=CancellationResponse)
def request_cancellation(
    body: CancellationRequest, state: AppState = Depends(get_app_state)
) -> CancellationResponse:
    try:
        new_state = state.cancellation.request_cancellation(body.business_operation_id)
    except ReceiverCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CancellationResponse(
        business_operation_id=body.business_operation_id, state=new_state.value
    )


@router.post("/confirm-from-status", response_model=CancellationResponse)
def confirm_from_status(
    body: CancellationRequest, state: AppState = Depends(get_app_state)
) -> CancellationResponse:
    try:
        new_state = state.cancellation.confirm_from_status(body.business_operation_id)
    except ReceiverCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CancellationResponse(
        business_operation_id=body.business_operation_id, state=new_state.value
    )