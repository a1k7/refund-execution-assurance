"""Authority revocation + policy configuration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..domain.enums import EvidenceEventType, InFlightPolicy
from ..domain.ids import new_correlation_id
from ..evidence.schema import build_event
from ..persistence.repositories import OperationRepository
from .deps import AppState, get_app_state
from .schemas import ConfigurePolicyRequest, RevokeRequest, RevokeResponse

router = APIRouter(prefix="/authority", tags=["authority"])


@router.post("/revoke", response_model=RevokeResponse)
def revoke(body: RevokeRequest, state: AppState = Depends(get_app_state)) -> RevokeResponse:
    # The RevocationService requires the receiver to propagate fencing. We pass it.
    ops = OperationRepository(state.db)
    bo_ids = [op.business_operation_id for op in ops.list_all()]
    # Caller supplies operation id in `source` per API contract? No — use body.source only.
    # We actually need to know which operation. Use body.source's target from query? Keep it
    # simple: require source to encode operation via dedicated endpoint below.
    raise HTTPException(
        status_code=400,
        detail="use POST /authority/revoke/{business_operation_id}",
    )


@router.post("/revoke/{business_operation_id}", response_model=RevokeResponse)
def revoke_operation(
    business_operation_id: str,
    body: RevokeRequest,
    state: AppState = Depends(get_app_state),
) -> RevokeResponse:
    op = OperationRepository(state.db).get(business_operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    if body.mode not in ("revoke", "supersede"):
        raise HTTPException(status_code=400, detail="invalid mode")
    try:
        if body.mode == "revoke":
            new_epoch = state.revocation.revoke(
                business_operation_id, source=body.source, receiver=state.receiver
            )
        else:
            new_epoch = state.revocation.supersede(
                business_operation_id, source=body.source, receiver=state.receiver
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    refreshed = OperationRepository(state.db).get(business_operation_id)
    assert refreshed is not None
    return RevokeResponse(
        business_operation_id=business_operation_id,
        new_epoch=new_epoch,
        authority_state=refreshed.authority_state.value,
        receiver_fence_updated=True,
    )


@router.post("/policy/{business_operation_id}")
def configure_policy(
    business_operation_id: str,
    body: ConfigurePolicyRequest,
    state: AppState = Depends(get_app_state),
) -> dict:
    try:
        policy = InFlightPolicy(body.in_flight_policy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ops = OperationRepository(state.db)
    op = ops.get(business_operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="operation_not_found")
    ops.update_fields(business_operation_id, state.clock.now(), in_flight_policy=policy)
    state.ledger.append(
        build_event(
            event_type=EvidenceEventType.POLICY_CONFIGURED,
            evidence_source="api",
            timestamp=state.clock.now(),
            business_operation_id=business_operation_id,
            previous_state=op.state,
            new_state=op.state,
            payload={"in_flight_policy": policy.value},
            correlation_id=new_correlation_id(),
        )
    )
    return {"business_operation_id": business_operation_id, "in_flight_policy": policy.value}