"""API request/response schemas (thin wrappers over domain models)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CreateOperationRequest(BaseModel):
    business_operation_id: Optional[str] = Field(None, description="Optional caller-supplied ID")
    in_flight_policy: str = "BLOCK_NEW_DISPATCHES"


class CreateOperationResponse(BaseModel):
    business_operation_id: str
    state: str
    in_flight_policy: str


class IssueReleaseRequest(BaseModel):
    business_operation_id: str
    subject: str = "agent-demo-1"
    audience: str = "decisionassure:refunds"
    capability: str = "refund.issue"
    policy_version: str = "policy-2024-01"
    amount: int = 1000
    currency: str = "USD"
    params_digest: str = "sha256:demo"
    valid_for_seconds: int = 300
    release_epoch: Optional[int] = None


class SignedReleaseView(BaseModel):
    payload: Dict[str, Any]
    signature: Dict[str, Any]


class RevokeRequest(BaseModel):
    source: str = "org-admin"
    mode: str = "revoke"  # or "supersede"


class RevokeResponse(BaseModel):
    business_operation_id: str
    new_epoch: int
    authority_state: str
    receiver_fence_updated: bool


class ConfigurePolicyRequest(BaseModel):
    in_flight_policy: str = "BLOCK_NEW_DISPATCHES"


class DispatchRequest(BaseModel):
    business_operation_id: str
    release: SignedReleaseView
    idempotency_key: str


class DispatchResponse(BaseModel):
    business_operation_id: str
    dispatch_attempt_id: str
    release_id: str
    release_epoch: int
    operation_state: str
    response_class: Optional[str]
    receiver_refund_reference: Optional[str]
    reason: Optional[str]


class CancellationRequest(BaseModel):
    business_operation_id: str


class CancellationResponse(BaseModel):
    business_operation_id: str
    state: str


class ReconciliationRequest(BaseModel):
    business_operation_id: str


class ReconciliationResponse(BaseModel):
    business_operation_id: str
    state: str


class OperationView(BaseModel):
    business_operation_id: str
    state: str
    authority_state: str
    in_flight_policy: str
    terminal_classification: Optional[str]
    retry_eligible: bool
    reconciliation_required: bool
    receiver_refund_reference: Optional[str]
    current_epoch: int


class AttemptView(BaseModel):
    dispatch_attempt_id: str
    release_id: str
    release_epoch: int
    idempotency_key: str
    state: str
    response_class: Optional[str]
    receiver_refund_reference: Optional[str]


class OperationHistoryResponse(BaseModel):
    operation: OperationView
    attempts: List[AttemptView]
    retry_decision: Dict[str, Any]


class EvidenceEventView(BaseModel):
    sequence: int
    event_id: str
    event_type: str
    business_operation_id: Optional[str]
    release_id: Optional[str]
    previous_state: Optional[str]
    new_state: Optional[str]
    timestamp: str
    payload: Optional[Dict[str, Any]]
    hash: str


class EvidenceResponse(BaseModel):
    business_operation_id: str
    chain_valid: bool
    events: List[EvidenceEventView]


class LedgerVerificationResponse(BaseModel):
    chain_valid: bool
    event_count: int


class VerificationReportView(BaseModel):
    verification_status: str
    business_operation_id: str
    terminal_classification: Optional[str]
    evidence_events_examined: int
    failed_assertions: List[str]
    warnings: List[str]
    unresolved_conditions: List[str]
    retry_eligible: bool
    reconciliation_required: bool
    chain_valid: bool


class RunScenarioRequest(BaseModel):
    scenario_id: str


class ScenarioResultView(BaseModel):
    scenario_id: str
    business_operation_id: str
    description: str
    operation: OperationView
    verification_report: VerificationReportView
    last_receiver_reported_status: Optional[str]
    notes: List[str] = []