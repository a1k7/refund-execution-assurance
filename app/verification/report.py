"""Report shaping helpers. Kept separate so the verifier stays focused on logic."""
from __future__ import annotations

from typing import List, Optional

from ..domain.enums import TerminalClassification, VerificationStatus
from ..domain.models import VerificationReport


def build_report(
    *,
    business_operation_id: str,
    verification_status: VerificationStatus,
    terminal_classification: Optional[TerminalClassification],
    evidence_events_examined: int,
    failed_assertions: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    unresolved_conditions: Optional[List[str]] = None,
    retry_eligible: bool,
    reconciliation_required: bool,
    chain_valid: bool,
    last_event_sequence: int,
    last_event_hash: Optional[str],
) -> VerificationReport:
    return VerificationReport(
        verification_status=verification_status,
        business_operation_id=business_operation_id,
        terminal_classification=terminal_classification,
        evidence_events_examined=evidence_events_examined,
        failed_assertions=failed_assertions or [],
        warnings=warnings or [],
        unresolved_conditions=unresolved_conditions or [],
        retry_eligible=retry_eligible,
        reconciliation_required=reconciliation_required,
        chain_valid=chain_valid,
        last_event_sequence=last_event_sequence,
        last_event_hash=last_event_hash,
    )