"""Dispatch endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..domain.models import ReleasePayload, ReleaseSignature, SignedRelease
from .deps import AppState, get_app_state
from .schemas import DispatchRequest, DispatchResponse

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


def _signed_release_from_view(body: DispatchRequest) -> SignedRelease:
    return SignedRelease(
        payload=ReleasePayload.model_validate(body.release.payload),
        signature=ReleaseSignature.model_validate(body.release.signature),
    )


@router.post("", response_model=DispatchResponse)
def dispatch(body: DispatchRequest, state: AppState = Depends(get_app_state)) -> DispatchResponse:
    try:
        signed = _signed_release_from_view(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid_release_shape:{exc}") from exc

    try:
        result = state.coordinator.dispatch(
            business_operation_id=body.business_operation_id,
            release=signed,
            idempotency_key=body.idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return DispatchResponse(
        business_operation_id=result.business_operation_id,
        dispatch_attempt_id=result.dispatch_attempt_id,
        release_id=result.release_id,
        release_epoch=result.release_epoch,
        operation_state=result.operation_state.value,
        response_class=result.response_class.value if result.response_class else None,
        receiver_refund_reference=result.receiver_refund_reference,
        reason=result.reason,
    )