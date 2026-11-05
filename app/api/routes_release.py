"""Release issuance + viewing. Simulated Parmana issuer."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..domain.enums import EvidenceEventType, OperationState
from ..domain.errors import NotFoundError
from ..domain.ids import new_business_operation_id, new_correlation_id
from ..domain.models import SignedRelease
from ..domain.state_machine import assert_transition
from ..evidence.schema import build_event
from ..persistence.repositories import OperationRepository, ReleaseRepository
from .deps import AppState, get_app_state
from .schemas import (
    CreateOperationRequest,
    CreateOperationResponse,
    IssueReleaseRequest,
    SignedReleaseView,
)

router = APIRouter(prefix="/release", tags=["release"])


@router.post("/operations", response_model=CreateOperationResponse)
def create_operation(
    body: CreateOperationRequest,
    state: AppState = Depends(get_app_state),
) -> CreateOperationResponse:
    from ..domain.enums import InFlightPolicy

    bo_id = body.business_operation_id or new_business_operation_id()
    try:
        policy = InFlightPolicy(body.in_flight_policy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ops = OperationRepository(state.db)
    existing = ops.get(bo_id)
    if existing is not None:
        return CreateOperationResponse(
            business_operation_id=existing.business_operation_id,
            state=existing.state.value,
            in_flight_policy=existing.in_flight_policy.value,
        )
    rec = ops.create(bo_id, state.clock.now(), policy)
    state.ledger.append(
        build_event(
            event_type=EvidenceEventType.OPERATION_CREATED,
            evidence_source="api",
            timestamp=state.clock.now(),
            business_operation_id=bo_id,
            previous_state=None,
            new_state=OperationState.CREATED,
            correlation_id=new_correlation_id(),
            payload={"in_flight_policy": policy.value},
        )
    )
    state.ledger.append(
        build_event(
            event_type=EvidenceEventType.POLICY_CONFIGURED,
            evidence_source="api",
            timestamp=state.clock.now(),
            business_operation_id=bo_id,
            previous_state=OperationState.CREATED,
            new_state=OperationState.CREATED,
            payload={"in_flight_policy": policy.value},
            correlation_id=new_correlation_id(),
        )
    )
    return CreateOperationResponse(
        business_operation_id=rec.business_operation_id,
        state=rec.state.value,
        in_flight_policy=rec.in_flight_policy.value,
    )


@router.post("/issue", response_model=SignedReleaseView)
def issue_release(
    body: IssueReleaseRequest,
    state: AppState = Depends(get_app_state),
) -> SignedReleaseView:
    ops = OperationRepository(state.db)
    op = ops.get(body.business_operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="operation_not_found")

    epoch = body.release_epoch if body.release_epoch is not None else op.current_epoch
    signed = state.issuer.issue(
        business_operation_id=body.business_operation_id,
        release_epoch=epoch,
        subject=body.subject,
        audience=body.audience,
        capability=body.capability,
        policy_version=body.policy_version,
        amount=body.amount,
        currency=body.currency,
        params_digest=body.params_digest,
        valid_for_seconds=body.valid_for_seconds,
    )
    ReleaseRepository(state.db).insert(signed, state.clock.now())
    if op.state in (OperationState.CREATED, OperationState.ADMISSIBILITY_DENIED, OperationState.DISPATCH_REJECTED):
        assert_transition(op.state, OperationState.RELEASE_ISSUED, reason="api-issue")
        ops.update_state(body.business_operation_id, OperationState.RELEASE_ISSUED, state.clock.now())
    state.ledger.append(
        build_event(
            event_type=EvidenceEventType.RELEASE_ISSUED,
            evidence_source="parmana-issuer",
            timestamp=state.clock.now(),
            business_operation_id=body.business_operation_id,
            release=signed.payload,
            previous_state=op.state,
            new_state=OperationState.RELEASE_ISSUED,
            correlation_id=new_correlation_id(),
        )
    )
    return SignedReleaseView(
        payload=signed.payload.model_dump(mode="json"),
        signature=signed.signature.model_dump(mode="json"),
    )


@router.get("/{release_id}", response_model=SignedReleaseView)
def get_release(release_id: str, state: AppState = Depends(get_app_state)) -> SignedReleaseView:
    repo = ReleaseRepository(state.db)
    row = repo.get(release_id)
    if row is None:
        raise HTTPException(status_code=404, detail="release_not_found")
    import json as _json
    return SignedReleaseView(
        payload=_json.loads(row["payload_json"]),
        signature=_json.loads(row["signature_json"]),
    )


__all__ = ["router", "NotFoundError", "SignedRelease"]