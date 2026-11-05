"""Evidence inspection endpoints (read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .deps import AppState, get_app_state
from .schemas import EvidenceEventView, EvidenceResponse, LedgerVerificationResponse

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("/ledger/verify", response_model=LedgerVerificationResponse)
def verify_ledger(state: AppState = Depends(get_app_state)) -> LedgerVerificationResponse:
    ok, count = state.verifier.verify_ledger()
    return LedgerVerificationResponse(chain_valid=ok, event_count=count)


@router.get("/{business_operation_id}", response_model=EvidenceResponse)
def get_evidence(
    business_operation_id: str, state: AppState = Depends(get_app_state)
) -> EvidenceResponse:
    rows = state.ledger.read_for_operation(business_operation_id)
    if not rows:
        raise HTTPException(status_code=404, detail="no_evidence")
    events = [
        EvidenceEventView(
            sequence=int(r["_sequence"]),
            event_id=r.get("event_id", ""),
            event_type=r.get("event_type", ""),
            business_operation_id=r.get("business_operation_id"),
            release_id=r.get("release_id"),
            previous_state=(r.get("previous_state") or None),
            new_state=(r.get("new_state") or None),
            timestamp=r.get("timestamp", ""),
            payload=r.get("payload"),
            hash=r["_hash"],
        )
        for r in rows
    ]
    return EvidenceResponse(
        business_operation_id=business_operation_id,
        chain_valid=state.ledger.verify_chain(),
        events=events,
    )