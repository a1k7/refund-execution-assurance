"""Application factory. Populates AppState and mounts all routers."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    routes_authority,
    routes_cancellation,
    routes_demo,
    routes_dispatch,
    routes_evidence,
    routes_operations,
    routes_reconciliation,
    routes_release,
    routes_verify,
)
from .api.deps import AppState
from .authority.revocation import RevocationService
from .cancellation.service import CancellationService
from .clock import SystemClock
from .config import load_settings
from .connector.dispatch_coordinator import DispatchCoordinator
from .connector.hooks import Hooks
from .connector.retry_policy import RetryPolicy
from .dashboard.routes import router as dashboard_router
from .evidence.ledger import EvidenceLedger
from .logging_config import configure_logging
from .persistence.db import Database
from .persistence.repositories import (
    DispatchAttemptRepository,
    OperationRepository,
)
from .reconciliation.service import ReconciliationService
from .receiver.simulated import SimulatedRefundReceiver
from .release.issuer import ParmanaReleaseIssuer
from .release.trust_store import TrustStore
from .release.verifier import ReleaseVerifier
from .verification.verifier import IndependentVerifier


def build_app(db_path: str | None = None) -> FastAPI:
    settings = load_settings()
    configure_logging(settings.log_level)

    clock = SystemClock()
    db = Database(db_path or settings.db_path)
    trust_store = TrustStore(settings.parmana_trust_store_path)
    issuer = ParmanaReleaseIssuer(
        key_id=settings.parmana_key_id,
        private_key_path=settings.parmana_private_key_path,
        trust_store=trust_store,
        clock=clock,
    )
    release_verifier = ReleaseVerifier(
        trust_store=trust_store,
        expected_audience="decisionassure:refunds",
        expected_capability="refund.issue",
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
    ops_repo = OperationRepository(db)
    attempts_repo = DispatchAttemptRepository(db)
    retry_policy = RetryPolicy(ops_repo, attempts_repo)

    app_state = AppState(
        db=db, clock=clock, receiver=receiver, trust_store=trust_store,
        issuer=issuer, release_verifier=release_verifier, revocation=revocation,
        ledger=ledger, coordinator=coordinator, cancellation=cancellation,
        reconciliation=reconciliation, verifier=verifier,
        operations=ops_repo, attempts=attempts_repo, retry_policy=retry_policy,
        hooks=hooks,
    )

    app = FastAPI(title="Refund Execution Assurance", version="0.1.0")
    app.state.app_state = app_state

    app.include_router(routes_release.router)
    app.include_router(routes_authority.router)
    app.include_router(routes_dispatch.router)
    app.include_router(routes_cancellation.router)
    app.include_router(routes_reconciliation.router)
    app.include_router(routes_operations.router)
    app.include_router(routes_evidence.router)
    app.include_router(routes_verify.router)
    app.include_router(routes_demo.router)
    app.include_router(dashboard_router)

    static_dir = Path(__file__).parent / "dashboard" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard")

    return app


app = build_app()