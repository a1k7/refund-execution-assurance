"""Domain models. Pydantic v2. All datetimes are tz-aware UTC."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .enums import (
    AuthorityState,
    EvidenceEventType,
    InFlightPolicy,
    OperationState,
    ReceiverResponseClass,
    RetryDecisionKind,
    TerminalClassification,
    VerificationStatus,
)


class ReleasePayload(BaseModel):
    """The canonical, signable portion of a Parmana release."""
    release_id: str
    release_epoch: int
    business_operation_id: str
    subject: str
    audience: str
    capability: str
    policy_version: str
    issued_at: datetime
    expires_at: datetime
    amount: int  # minor units (e.g. cents)
    currency: str
    params_digest: str


class ReleaseSignature(BaseModel):
    key_id: str
    alg: Literal["Ed25519"] = "Ed25519"
    signature_b64: str


class SignedRelease(BaseModel):
    payload: ReleasePayload
    signature: ReleaseSignature


class OperationRecord(BaseModel):
    business_operation_id: str
    state: OperationState
    authority_state: AuthorityState
    in_flight_policy: InFlightPolicy
    terminal_classification: Optional[TerminalClassification] = None
    retry_eligible: bool = False
    reconciliation_required: bool = False
    receiver_refund_reference: Optional[str] = None
    current_epoch: int = 0
    created_at: datetime
    updated_at: datetime


class DispatchAttemptRecord(BaseModel):
    dispatch_attempt_id: str
    business_operation_id: str
    release_id: str
    release_epoch: int
    idempotency_key: str
    state: OperationState
    response_class: Optional[ReceiverResponseClass] = None
    receiver_refund_reference: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RevocationRecord(BaseModel):
    id: int
    business_operation_id: str
    revoked_at: datetime
    source: str
    new_epoch: int


class ReceiverResponse(BaseModel):
    """Return type from any receiver call. The receiver only reports what it wants to disclose."""
    response_class: ReceiverResponseClass
    receiver_refund_reference: Optional[str] = None
    detail: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class DispatchResult(BaseModel):
    business_operation_id: str
    dispatch_attempt_id: str
    release_id: str
    release_epoch: int
    operation_state: OperationState
    response_class: Optional[ReceiverResponseClass]
    receiver_refund_reference: Optional[str] = None
    reason: Optional[str] = None


class RetryDecision(BaseModel):
    decision: RetryDecisionKind
    reason: str
    last_attempt_id: Optional[str] = None
    last_response_class: Optional[ReceiverResponseClass] = None


class EvidenceEvent(BaseModel):
    """One evidence record. Every field maps to a column in EVIDENCE_SPEC.md."""
    event_id: str
    event_type: EvidenceEventType
    schema_version: str = "1.0"
    business_operation_id: Optional[str] = None
    release_id: Optional[str] = None
    release_epoch: Optional[int] = None
    dispatch_attempt_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    receiver_refund_reference: Optional[str] = None
    subject: Optional[str] = None
    audience: Optional[str] = None
    capability: Optional[str] = None
    policy_version: Optional[str] = None
    authority_state: Optional[AuthorityState] = None
    timestamp: datetime
    previous_state: Optional[OperationState] = None
    new_state: Optional[OperationState] = None
    receiver_response_class: Optional[ReceiverResponseClass] = None
    evidence_source: str
    payload_digest: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    sequence: Optional[int] = None


class VerificationReport(BaseModel):
    verification_status: VerificationStatus
    business_operation_id: str
    terminal_classification: Optional[TerminalClassification]
    evidence_events_examined: int
    failed_assertions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    unresolved_conditions: List[str] = Field(default_factory=list)
    retry_eligible: bool = False
    reconciliation_required: bool = False
    chain_valid: bool = True
    last_event_sequence: int = 0
    last_event_hash: Optional[str] = None