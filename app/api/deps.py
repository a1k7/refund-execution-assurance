"""Dependency container for the FastAPI app.

A single ``AppState`` instance is populated by ``app/main.py`` and stored on
``app.state.app_state``. Routes access it via ``Depends(get_app_state)``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from ..authority.revocation import RevocationService
from ..cancellation.service import CancellationService
from ..clock import Clock
from ..connector.dispatch_coordinator import DispatchCoordinator
from ..connector.hooks import Hooks
from ..connector.retry_policy import RetryPolicy
from ..evidence.ledger import EvidenceLedger
from ..persistence.db import Database
from ..persistence.repositories import DispatchAttemptRepository, OperationRepository
from ..reconciliation.service import ReconciliationService
from ..receiver.simulated import SimulatedRefundReceiver
from ..release.issuer import ParmanaReleaseIssuer
from ..release.trust_store import TrustStore
from ..release.verifier import ReleaseVerifier
from ..verification.verifier import IndependentVerifier


@dataclass
class AppState:
    db: Database
    clock: Clock
    receiver: SimulatedRefundReceiver
    trust_store: TrustStore
    issuer: ParmanaReleaseIssuer
    release_verifier: ReleaseVerifier
    revocation: RevocationService
    ledger: EvidenceLedger
    coordinator: DispatchCoordinator
    cancellation: CancellationService
    reconciliation: ReconciliationService
    verifier: IndependentVerifier
    operations: OperationRepository
    attempts: DispatchAttemptRepository
    retry_policy: RetryPolicy
    hooks: Hooks


def get_app_state(request: Request) -> AppState:
    state: Optional[AppState] = getattr(request.app.state, "app_state", None)
    if state is None:
        raise RuntimeError("AppState not initialized; see app/main.py build_app()")
    return state