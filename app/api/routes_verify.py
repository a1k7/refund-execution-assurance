"""Independent verifier endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .deps import AppState, get_app_state
from .schemas import VerificationReportView

router = APIRouter(prefix="/verify", tags=["verify"])


@router.get("/{business_operation_id}", response_model=VerificationReportView)
def verify_operation(
    business_operation_id: str, state: AppState = Depends(get_app_state)
) -> VerificationReportView:
    report = state.verifier.verify(business_operation_id)
    return VerificationReportView(
        verification_status=report.verification_status.value,
        business_operation_id=report.business_operation_id,
        terminal_classification=report.terminal_classification.value if report.terminal_classification else None,
        evidence_events_examined=report.evidence_events_examined,
        failed_assertions=report.failed_assertions,
        warnings=report.warnings,
        unresolved_conditions=report.unresolved_conditions,
        retry_eligible=report.retry_eligible,
        reconciliation_required=report.reconciliation_required,
        chain_valid=report.chain_valid,
    )