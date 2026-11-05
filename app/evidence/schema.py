"""Evidence event schema and canonical payload builder.

Every persisted event contains exactly the fields enumerated here. Adding a field
means bumping ``SCHEMA_VERSION`` — kept conservative for now.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from ..domain.enums import (
    AuthorityState,
    EvidenceEventType,
    OperationState,
    ReceiverResponseClass,
)
from ..domain.ids import new_event_id
from ..domain.models import EvidenceEvent, ReleasePayload

SCHEMA_VERSION = "1.0"


def build_event(
    *,
    event_type: EvidenceEventType,
    evidence_source: str,
    timestamp: datetime,
    business_operation_id: Optional[str] = None,
    release: Optional[ReleasePayload] = None,
    release_id: Optional[str] = None,
    release_epoch: Optional[int] = None,
    dispatch_attempt_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    receiver_refund_reference: Optional[str] = None,
    authority_state: Optional[AuthorityState] = None,
    previous_state: Optional[OperationState] = None,
    new_state: Optional[OperationState] = None,
    receiver_response_class: Optional[ReceiverResponseClass] = None,
    payload: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
    payload_digest: Optional[str] = None,
) -> EvidenceEvent:
    subject = audience = capability = policy_version = None
    if release is not None:
        business_operation_id = business_operation_id or release.business_operation_id
        release_id = release_id or release.release_id
        release_epoch = release_epoch if release_epoch is not None else release.release_epoch
        subject = release.subject
        audience = release.audience
        capability = release.capability
        policy_version = release.policy_version

    return EvidenceEvent(
        event_id=new_event_id(),
        event_type=event_type,
        schema_version=SCHEMA_VERSION,
        business_operation_id=business_operation_id,
        release_id=release_id,
        release_epoch=release_epoch,
        dispatch_attempt_id=dispatch_attempt_id,
        idempotency_key=idempotency_key,
        receiver_refund_reference=receiver_refund_reference,
        subject=subject,
        audience=audience,
        capability=capability,
        policy_version=policy_version,
        authority_state=authority_state,
        timestamp=timestamp,
        previous_state=previous_state,
        new_state=new_state,
        receiver_response_class=receiver_response_class,
        evidence_source=evidence_source,
        payload_digest=payload_digest,
        payload=payload,
        correlation_id=correlation_id,
    )


def to_storage_payload(event: EvidenceEvent) -> Dict[str, Any]:
    """Return the exact dict that will be canonicalized and hashed.

    We strip any None values to keep the payloads compact and stable.
    """
    d = event.model_dump(mode="json")
    return {k: v for k, v in d.items() if v is not None}


def from_storage_payload(payload: Dict[str, Any]) -> EvidenceEvent:
    return EvidenceEvent.model_validate(payload)