"""Build a fully-wired test world with fresh, isolated state.

Every ``World`` instance owns:
  * a file-backed SQLite database in a per-test tmp directory
  * a fresh Ed25519 issuer + matching trust store
  * a fresh simulated receiver
  * a fresh verifier, coordinator, cancellation service, reconciliation service
  * a test-only ``ReceiverOracle``

The world also exposes small helpers (``create_operation``, ``issue_release``)
that mirror what the API routes do, so unit tests read naturally.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.authority.revocation import RevocationService
from app.cancellation.service import CancellationService
from app.clock import FakeClock
from app.connector.dispatch_coordinator import DispatchCoordinator
from app.connector.hooks import Hooks
from app.connector.retry_policy import RetryPolicy
from app.domain.enums import (
    EvidenceEventType,
    InFlightPolicy,
    OperationState,
)
from app.domain.ids import new_business_operation_id, new_correlation_id
from app.domain.models import SignedRelease
from app.evidence.ledger import EvidenceLedger
from app.evidence.schema import build_event
from app.persistence.db import Database
from app.persistence.repositories import (
    DispatchAttemptRepository,
    OperationRepository,
    ReleaseRepository,
)
from app.receiver.simulated import SimulatedRefundReceiver
from app.reconciliation.service import ReconciliationService
from app.release.issuer import ParmanaReleaseIssuer
from app.release.trust_store import TrustStore
from app.release.verifier import ReleaseVerifier
from app.verification.verifier import IndependentVerifier

from .receiver_oracle import ReceiverOracle


EXPECTED_AUDIENCE = "decisionassure:refunds"
EXPECTED_CAPABILITY = "refund.issue"


@dataclass
class World:
    tmp_path: Path
    db: Database
    clock: FakeClock
    trust_store: TrustStore
    issuer: ParmanaReleaseIssuer
    release_verifier: ReleaseVerifier
    receiver: SimulatedRefundReceiver
    ledger: EvidenceLedger
    revocation: RevocationService
    hooks: Hooks
    coordinator: DispatchCoordinator
    cancellation: CancellationService
    reconciliation: ReconciliationService
    verifier: IndependentVerifier
    operations: OperationRepository
    attempts: DispatchAttemptRepository
    oracle: ReceiverOracle

    # --- helpers ----------------------------------------------------

    def create_operation(
        self,
        business_operation_id: Optional[str] = None,
        policy: InFlightPolicy = InFlightPolicy.BLOCK_NEW_DISPATCHES,
    ) -> str:
        bo_id = business_operation_id or new_business_operation_id()
        self.operations.create(bo_id, self.clock.now(), policy)
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.OPERATION_CREATED,
                evidence_source="tests",
                timestamp=self.clock.now(),
                business_operation_id=bo_id,
                new_state=OperationState.CREATED,
                payload={"in_flight_policy": policy.value},
                correlation_id=new_correlation_id(),
            )
        )
        return bo_id

    def issue_release(
        self,
        business_operation_id: str,
        *,
        release_epoch: Optional[int] = None,
        subject: str = "agent-test",
        audience: str = EXPECTED_AUDIENCE,
        capability: str = EXPECTED_CAPABILITY,
        policy_version: str = "policy-test",
        amount: int = 1000,
        currency: str = "USD",
        params_digest: str = "sha256:test",
        valid_for_seconds: int = 300,
    ) -> SignedRelease:
        op = self.operations.get(business_operation_id)
        assert op is not None, f"unknown operation {business_operation_id}"
        epoch = release_epoch if release_epoch is not None else op.current_epoch
        signed = self.issuer.issue(
            business_operation_id=business_operation_id,
            release_epoch=epoch,
            subject=subject,
            audience=audience,
            capability=capability,
            policy_version=policy_version,
            amount=amount,
            currency=currency,
            params_digest=params_digest,
            valid_for_seconds=valid_for_seconds,
        )
        ReleaseRepository(self.db).insert(signed, self.clock.now())
        if op.state is OperationState.CREATED:
            self.operations.update_state(
                business_operation_id, OperationState.RELEASE_ISSUED, self.clock.now()
            )
        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.RELEASE_ISSUED,
                evidence_source="tests",
                timestamp=self.clock.now(),
                business_operation_id=business_operation_id,
                release=signed.payload,
                previous_state=op.state,
                new_state=OperationState.RELEASE_ISSUED,
                correlation_id=new_correlation_id(),
            )
        )
        return signed


def build_world(tmp_path: Path) -> World:
    db = Database(str(tmp_path / "raa_test.db"))
    clock = FakeClock()
    trust_store = TrustStore(str(tmp_path / "parmana_trust.json"))
    issuer = ParmanaReleaseIssuer(
        key_id="test-key",
        private_key_path=str(tmp_path / "parmana_private.pem"),
        trust_store=trust_store,
        clock=clock,
    )
    release_verifier = ReleaseVerifier(
        trust_store=trust_store,
        expected_audience=EXPECTED_AUDIENCE,
        expected_capability=EXPECTED_CAPABILITY,
    )
    receiver = SimulatedRefundReceiver(clock=clock)
    ledger = EvidenceLedger(db)
    revocation = RevocationService(db, clock)
    hooks = Hooks()
    coordinator = DispatchCoordinator(
        db=db,
        clock=clock,
        release_verifier=release_verifier,
        revocation=revocation,
        receiver=receiver,
        hooks=hooks,
    )
    cancellation = CancellationService(db, clock, receiver)
    reconciliation = ReconciliationService(db, clock, receiver)
    verifier = IndependentVerifier(ledger)
    operations = OperationRepository(db)
    attempts = DispatchAttemptRepository(db)

    return World(
        tmp_path=tmp_path,
        db=db,
        clock=clock,
        trust_store=trust_store,
        issuer=issuer,
        release_verifier=release_verifier,
        receiver=receiver,
        ledger=ledger,
        revocation=revocation,
        hooks=hooks,
        coordinator=coordinator,
        cancellation=cancellation,
        reconciliation=reconciliation,
        verifier=verifier,
        operations=operations,
        attempts=attempts,
        oracle=ReceiverOracle(receiver),
    )


__all__ = ["World", "build_world", "EXPECTED_AUDIENCE", "EXPECTED_CAPABILITY"]