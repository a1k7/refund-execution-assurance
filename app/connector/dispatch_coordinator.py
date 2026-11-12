"""Dispatch coordinator: the one place that drives a refund dispatch end to end.
...
"""
from __future__ import annotations

import threading
from typing import Dict, Optional

from ..authority.revocation import RevocationService
from ..clock import Clock
from ..domain.enums import (
    AuthorityState,
    EvidenceEventType,
    OperationState,
    ReceiverResponseClass,
    RetryDecisionKind,
    TerminalClassification,
)
from ..domain.errors import (
    DuplicateOperationError,
    IllegalTransition,
    NotFoundError,
    ReleaseVerificationError,
)
from ..domain.ids import new_correlation_id, new_dispatch_attempt_id
from ..domain.models import DispatchResult, ReceiverResponse, SignedRelease
from ..evidence.ledger import EvidenceLedger
from ..evidence.schema import build_event
from ..persistence.db import Database
from ..persistence.repositories import (
    DispatchAttemptRepository,
    OperationRepository,
    ReleaseRepository,
)
from ..receiver.protocol import ReceiverProtocol
from ..release.verifier import ReleaseVerifier
from .atomic_consumption import AtomicReleaseConsumption
from .duplicate_guard import DuplicateGuard
from .hooks import DispatchContext, Hooks


_REJECT_CLASSES = {
    ReceiverResponseClass.REJECTED_FENCED,
    ReceiverResponseClass.REJECTED_DUPLICATE,
    ReceiverResponseClass.REJECTED_INVALID,
}


class DispatchCoordinator:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        release_verifier: ReleaseVerifier,
        revocation: RevocationService,
        receiver: ReceiverProtocol,
        hooks: Optional[Hooks] = None,
    ) -> None:
        self.db = db
        self.clock = clock
        self.release_verifier = release_verifier
        self.revocation = revocation
        self.receiver = receiver
        self.hooks = hooks or Hooks()
        self.ops = OperationRepository(db)
        self.releases = ReleaseRepository(db)
        self.attempts = DispatchAttemptRepository(db)
        self.ledger = EvidenceLedger(db)
        self.consumption = AtomicReleaseConsumption(self.releases)
        self.duplicate_guard = DuplicateGuard(db, clock)
        self._dispatch_locks: Dict[str, threading.Lock] = {}
        self._dispatch_locks_mutex = threading.Lock()

    def _dispatch_lock(self, business_operation_id: str) -> threading.Lock:
        with self._dispatch_locks_mutex:
            lock = self._dispatch_locks.get(business_operation_id)
            if lock is None:
                lock = threading.Lock()
                self._dispatch_locks[business_operation_id] = lock
            return lock

    def dispatch(
        self,
        *,
        business_operation_id: str,
        release: SignedRelease,
        idempotency_key: str,
    ) -> DispatchResult:
        with self._dispatch_lock(business_operation_id):
            return self._dispatch_locked(
                business_operation_id=business_operation_id,
                release=release,
                idempotency_key=idempotency_key,
            )

    def _dispatch_locked(
        self,
        *,
        business_operation_id: str,
        release: SignedRelease,
        idempotency_key: str,
    ) -> DispatchResult:
        op = self.ops.get(business_operation_id)
        if op is None:
            raise NotFoundError(business_operation_id)

        correlation_id = new_correlation_id()

        # 1. Cryptographic release verification.
        try:
            self.release_verifier.verify(
                release,
                now=self.clock.now(),
                expected_business_operation_id=business_operation_id,
            )
        except ReleaseVerificationError as exc:
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.RELEASE_VERIFICATION_FAILED,
                    evidence_source="dispatch-coordinator",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    release=release.payload,
                    authority_state=op.authority_state,
                    previous_state=op.state,
                    new_state=op.state,
                    payload={"reason": str(exc)},
                    correlation_id=correlation_id,
                )
            )
            return DispatchResult(
                business_operation_id=business_operation_id,
                dispatch_attempt_id="",
                release_id=release.payload.release_id,
                release_epoch=release.payload.release_epoch,
                operation_state=op.state,
                response_class=None,
                reason=f"release_verification_failed:{exc}",
            )

        existing = self.releases.get(release.payload.release_id)
        if existing is None:
            self.releases.insert(release, self.clock.now())

        # 2. Advance to RELEASE_VERIFIED.
        if op.state in (OperationState.CREATED, OperationState.RELEASE_ISSUED):
            if op.state == OperationState.CREATED:
                self._safe_transition(
                    business_operation_id, op.state, OperationState.RELEASE_ISSUED,
                    correlation_id=correlation_id,
                )
                op = self.ops.get(business_operation_id)
            assert op is not None
            self._safe_transition(
                business_operation_id, op.state, OperationState.RELEASE_VERIFIED,
                correlation_id=correlation_id,
            )
            op = self.ops.get(business_operation_id)
        self.releases.mark_verified(
            release.payload.release_id, True, "signature_and_policy_ok", self.clock.now()
        )
        assert op is not None
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.RELEASE_VERIFIED,
                evidence_source="dispatch-coordinator",
                timestamp=self.clock.now(),
                business_operation_id=business_operation_id,
                release=release.payload,
                authority_state=op.authority_state,
                previous_state=op.state,
                new_state=OperationState.RELEASE_VERIFIED,
                correlation_id=correlation_id,
            )
        )

        # 3. Authority gate FIRST.
        revoked = self.revocation.is_revoked(business_operation_id)
        stale_epoch = release.payload.release_epoch < op.current_epoch
        if revoked or stale_epoch:
            reason = "authority_revoked" if revoked else "stale_epoch"
            self._safe_transition(
                business_operation_id, op.state, OperationState.ADMISSIBILITY_DENIED,
                correlation_id=correlation_id,
            )
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.ADMISSIBILITY_DENIED,
                    evidence_source="dispatch-coordinator",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    release=release.payload,
                    authority_state=AuthorityState.REVOKED if revoked else op.authority_state,
                    previous_state=op.state,
                    new_state=OperationState.ADMISSIBILITY_DENIED,
                    payload={"reason": reason, "op_current_epoch": op.current_epoch},
                    correlation_id=correlation_id,
                )
            )
            return DispatchResult(
                business_operation_id=business_operation_id,
                dispatch_attempt_id="",
                release_id=release.payload.release_id,
                release_epoch=release.payload.release_epoch,
                operation_state=OperationState.ADMISSIBILITY_DENIED,
                response_class=None,
                reason=reason,
            )

        # 4. Duplicate guard.
        decision = self.duplicate_guard.evaluate(business_operation_id)
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.RETRY_DECISION,
                evidence_source="duplicate-guard",
                timestamp=self.clock.now(),
                business_operation_id=business_operation_id,
                release=release.payload,
                authority_state=op.authority_state,
                previous_state=op.state,
                new_state=op.state,
                payload={"decision": decision.decision.value, "reason": decision.reason},
                correlation_id=correlation_id,
            )
        )
        if decision.decision != RetryDecisionKind.ALLOW:
            return DispatchResult(
                business_operation_id=business_operation_id,
                dispatch_attempt_id="",
                release_id=release.payload.release_id,
                release_epoch=release.payload.release_epoch,
                operation_state=op.state,
                response_class=None,
                reason=f"duplicate_guard:{decision.decision.value}:{decision.reason}",
            )

        # 5. Advance to ADMISSIBILITY_ALLOWED.
        if op.state == OperationState.RELEASE_VERIFIED:
            self._safe_transition(
                business_operation_id, OperationState.RELEASE_VERIFIED,
                OperationState.ADMISSIBILITY_ALLOWED, correlation_id=correlation_id,
            )
            op = self.ops.get(business_operation_id)
            assert op is not None
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.ADMISSIBILITY_ALLOWED,
                    evidence_source="dispatch-coordinator",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    release=release.payload,
                    authority_state=op.authority_state,
                    previous_state=OperationState.RELEASE_VERIFIED,
                    new_state=OperationState.ADMISSIBILITY_ALLOWED,
                    correlation_id=correlation_id,
                )
            )

        # 6. Create dispatch attempt ID.
        dispatch_attempt_id = new_dispatch_attempt_id()

        # 7. Atomic consumption.
        consumed = self.consumption.try_consume(
            release.payload.release_id, dispatch_attempt_id, self.clock.now()
        )
        if not consumed:
            self.attempts.create(
                dispatch_attempt_id=dispatch_attempt_id,
                business_operation_id=business_operation_id,
                release_id=release.payload.release_id,
                release_epoch=release.payload.release_epoch,
                idempotency_key=idempotency_key,
                state=OperationState.DISPATCH_REJECTED,
                now=self.clock.now(),
            )
            # We do NOT log DISPATCH_ATTEMPTED. The release was never consumed, 
            # so no real dispatch attempt occurred.
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.RELEASE_REPLAY_ATTEMPTED,
                    evidence_source="atomic-consumption",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    release=release.payload,
                    dispatch_attempt_id=dispatch_attempt_id,
                    idempotency_key=idempotency_key,
                    authority_state=op.authority_state,
                    previous_state=OperationState.ADMISSIBILITY_ALLOWED,
                    new_state=OperationState.DISPATCH_REJECTED,
                    payload={"reason": "release_already_consumed"},
                    correlation_id=correlation_id,
                )
            )
            return DispatchResult(
                business_operation_id=business_operation_id,
                dispatch_attempt_id=dispatch_attempt_id,
                release_id=release.payload.release_id,
                release_epoch=release.payload.release_epoch,
                operation_state=OperationState.ADMISSIBILITY_ALLOWED,
                response_class=None,
                reason="release_already_consumed",
            )

        # 8. Post-consumption transitions.
        self.attempts.create(
            dispatch_attempt_id=dispatch_attempt_id,
            business_operation_id=business_operation_id,
            release_id=release.payload.release_id,
            release_epoch=release.payload.release_epoch,
            idempotency_key=idempotency_key,
            state=OperationState.DISPATCH_ATTEMPTED,
            now=self.clock.now(),
        )
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.RELEASE_CONSUMED,
                evidence_source="atomic-consumption",
                timestamp=self.clock.now(),
                business_operation_id=business_operation_id,
                release=release.payload,
                dispatch_attempt_id=dispatch_attempt_id,
                idempotency_key=idempotency_key,
                authority_state=op.authority_state,
                previous_state=OperationState.ADMISSIBILITY_ALLOWED,
                new_state=OperationState.RELEASE_CONSUMED,
                correlation_id=correlation_id,
            )
        )
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.DISPATCH_ATTEMPTED,
                evidence_source="dispatch-coordinator",
                timestamp=self.clock.now(),
                business_operation_id=business_operation_id,
                release=release.payload,
                dispatch_attempt_id=dispatch_attempt_id,
                idempotency_key=idempotency_key,
                authority_state=op.authority_state,
                previous_state=OperationState.RELEASE_CONSUMED,
                new_state=OperationState.DISPATCH_ATTEMPTED,
                correlation_id=correlation_id,
            )
        )
        self._safe_transition(
            business_operation_id, OperationState.ADMISSIBILITY_ALLOWED,
            OperationState.RELEASE_CONSUMED, correlation_id=correlation_id,
        )
        self._safe_transition(
            business_operation_id, OperationState.RELEASE_CONSUMED,
            OperationState.DISPATCH_ATTEMPTED, correlation_id=correlation_id,
        )
        op = self.ops.get(business_operation_id)
        assert op is not None

        # 9. Hook.
        ctx = DispatchContext(
            business_operation_id=business_operation_id,
            release_id=release.payload.release_id,
            release_epoch=release.payload.release_epoch,
            dispatch_attempt_id=dispatch_attempt_id,
            idempotency_key=idempotency_key,
        )
        self.hooks.fire_before_receiver_call(ctx)

        # 10. Call the receiver.
        try:
            response = self.receiver.accept(
                business_operation_id=business_operation_id,
                release_id=release.payload.release_id,
                release_epoch=release.payload.release_epoch,
                idempotency_key=idempotency_key,
                amount=release.payload.amount,
                currency=release.payload.currency,
            )
        except Exception as exc:  # noqa: BLE001
            response = ReceiverResponse(
                response_class=ReceiverResponseClass.TRANSPORT_ERROR,
                detail=f"receiver_exception:{type(exc).__name__}:{exc}",
            )

        self.hooks.fire_after_receiver_response(ctx, response)

        # 11. Handle response.
        return self._handle_response(
            op_before_response=op,
            release=release,
            dispatch_attempt_id=dispatch_attempt_id,
            idempotency_key=idempotency_key,
            response=response,
            correlation_id=correlation_id,
        )

    def _handle_response(
        self,
        *,
        op_before_response,
        release: SignedRelease,
        dispatch_attempt_id: str,
        idempotency_key: str,
        response: ReceiverResponse,
        correlation_id: str,
    ) -> DispatchResult:
        business_operation_id = release.payload.business_operation_id
        now = self.clock.now()

        if response.response_class == ReceiverResponseClass.ACCEPTED:
            self.attempts.update(
                dispatch_attempt_id,
                now,
                state=OperationState.DISPATCH_ACCEPTED,
                response_class=ReceiverResponseClass.ACCEPTED,
                receiver_refund_reference=response.receiver_refund_reference,
            )
            self.ops.update_fields(
                business_operation_id,
                now,
                receiver_refund_reference=response.receiver_refund_reference,
            )
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.DISPATCH_ACCEPTED,
                    evidence_source="dispatch-coordinator",
                    timestamp=now,
                    business_operation_id=business_operation_id,
                    release=release.payload,
                    dispatch_attempt_id=dispatch_attempt_id,
                    idempotency_key=idempotency_key,
                    receiver_refund_reference=response.receiver_refund_reference,
                    receiver_response_class=ReceiverResponseClass.ACCEPTED,
                    authority_state=op_before_response.authority_state,
                    previous_state=OperationState.DISPATCH_ATTEMPTED,
                    new_state=OperationState.DISPATCH_ACCEPTED,
                    payload=response.model_dump(mode="json"),
                    correlation_id=correlation_id,
                )
            )
            self._safe_transition(
                business_operation_id, OperationState.DISPATCH_ATTEMPTED,
                OperationState.DISPATCH_ACCEPTED, correlation_id=correlation_id,
            )
            self._safe_transition(
                business_operation_id, OperationState.DISPATCH_ACCEPTED,
                OperationState.IN_FLIGHT, correlation_id=correlation_id,
            )
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.IN_FLIGHT,
                    evidence_source="dispatch-coordinator",
                    timestamp=now,
                    business_operation_id=business_operation_id,
                    release=release.payload,
                    dispatch_attempt_id=dispatch_attempt_id,
                    idempotency_key=idempotency_key,
                    receiver_refund_reference=response.receiver_refund_reference,
                    authority_state=op_before_response.authority_state,
                    previous_state=OperationState.DISPATCH_ACCEPTED,
                    new_state=OperationState.IN_FLIGHT,
                    correlation_id=correlation_id,
                )
            )
            return DispatchResult(
                business_operation_id=business_operation_id,
                dispatch_attempt_id=dispatch_attempt_id,
                release_id=release.payload.release_id,
                release_epoch=release.payload.release_epoch,
                operation_state=OperationState.IN_FLIGHT,
                response_class=response.response_class,
                receiver_refund_reference=response.receiver_refund_reference,
            )

        if response.response_class in _REJECT_CLASSES:
            self.attempts.update(
                dispatch_attempt_id,
                now,
                state=OperationState.DISPATCH_REJECTED,
                response_class=response.response_class,
                receiver_refund_reference=response.receiver_refund_reference,
            )
            self._safe_transition(
                business_operation_id, OperationState.DISPATCH_ATTEMPTED,
                OperationState.DISPATCH_REJECTED, correlation_id=correlation_id,
            )
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.DISPATCH_REJECTED,
                    evidence_source="dispatch-coordinator",
                    timestamp=now,
                    business_operation_id=business_operation_id,
                    release=release.payload,
                    dispatch_attempt_id=dispatch_attempt_id,
                    idempotency_key=idempotency_key,
                    receiver_refund_reference=response.receiver_refund_reference,
                    receiver_response_class=response.response_class,
                    authority_state=op_before_response.authority_state,
                    previous_state=OperationState.DISPATCH_ATTEMPTED,
                    new_state=OperationState.DISPATCH_REJECTED,
                    payload=response.model_dump(mode="json"),
                    correlation_id=correlation_id,
                )
            )
            return DispatchResult(
                business_operation_id=business_operation_id,
                dispatch_attempt_id=dispatch_attempt_id,
                release_id=release.payload.release_id,
                release_epoch=release.payload.release_epoch,
                operation_state=OperationState.DISPATCH_REJECTED,
                response_class=response.response_class,
                reason=response.detail,
            )

        # INCONCLUSIVE / TRANSPORT_ERROR -> OUTCOME_UNKNOWN.
        self.attempts.update(
            dispatch_attempt_id,
            now,
            state=OperationState.OUTCOME_UNKNOWN,
            response_class=response.response_class,
            receiver_refund_reference=response.receiver_refund_reference,
        )
        self._safe_transition(
            business_operation_id, OperationState.DISPATCH_ATTEMPTED,
            OperationState.OUTCOME_UNKNOWN, correlation_id=correlation_id,
        )
        update_fields = {
            "terminal_classification": TerminalClassification.OUTCOME_UNKNOWN,
            "reconciliation_required": True,
            "retry_eligible": False,
        }
        if response.receiver_refund_reference is not None:
            update_fields["receiver_refund_reference"] = response.receiver_refund_reference
        self.ops.update_fields(business_operation_id, now, **update_fields)
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.OUTCOME_UNKNOWN,
                evidence_source="dispatch-coordinator",
                timestamp=now,
                business_operation_id=business_operation_id,
                release=release.payload,
                dispatch_attempt_id=dispatch_attempt_id,
                idempotency_key=idempotency_key,
                receiver_refund_reference=response.receiver_refund_reference,
                receiver_response_class=response.response_class,
                authority_state=op_before_response.authority_state,
                previous_state=OperationState.DISPATCH_ATTEMPTED,
                new_state=OperationState.OUTCOME_UNKNOWN,
                payload=response.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
        )
        return DispatchResult(
            business_operation_id=business_operation_id,
            dispatch_attempt_id=dispatch_attempt_id,
            release_id=release.payload.release_id,
            release_epoch=release.payload.release_epoch,
            operation_state=OperationState.OUTCOME_UNKNOWN,
            response_class=response.response_class,
            receiver_refund_reference=response.receiver_refund_reference,
            reason=response.detail or "inconclusive",
        )

    def _safe_transition(
        self,
        business_operation_id: str,
        from_state: OperationState,
        to_state: OperationState,
        *,
        correlation_id: str,
    ) -> None:
        from ..domain.state_machine import assert_transition

        try:
            assert_transition(from_state, to_state, reason="dispatch-coordinator")
        except IllegalTransition as exc:
            self.ledger.append(
                build_event(
                    event_type=EvidenceEventType.GUARD_VIOLATED,
                    evidence_source="dispatch-coordinator",
                    timestamp=self.clock.now(),
                    business_operation_id=business_operation_id,
                    payload={"from": from_state.value, "to": to_state.value, "error": str(exc)},
                    correlation_id=correlation_id,
                )
            )
            raise
        self.ops.update_state(business_operation_id, to_state, self.clock.now())


__all__ = ["DispatchCoordinator", "DuplicateOperationError"]