"""Scenario runner. Bridges the API/CLI to ``app/demo/scenarios.py``."""
from __future__ import annotations

from typing import List

from .scenarios import SCENARIOS, ScenarioWorld


_DESCRIPTIONS = {
    "01": "Revocation before dispatch",
    "02": "Revocation before connector check",
    "03": "Revocation between check and receiver acceptance",
    "04": "Revocation + cancellation confirmed",
    "05": "Completion before cancellation",
    "06": "Cancellation ack, terminal unknown",
    "07": "Receiver completed but reported inconclusive",
    "08": "Default BLOCK_NEW_DISPATCHES policy",
    "09": "New idempotency key cannot bypass duplicate guard",
    "10": "Concurrent dispatch of the same release",
    "11": "Replay of a consumed release",
    "12": "Expired / altered / invalid signed releases",
    "13": "Cancellation unsupported by receiver",
    "14": "Evidence tampering detection",
    "15": "Reconciliation resolves unknown outcome",
    "16": "Stale epoch rejected by connector and receiver",
}


def list_scenarios() -> List[dict]:
    return [{"scenario_id": k, "description": v} for k, v in sorted(_DESCRIPTIONS.items())]


def run_scenario(app_state, scenario_id: str) -> dict:
    if scenario_id not in SCENARIOS:
        raise KeyError(scenario_id)

    world = ScenarioWorld(
        db=app_state.db,
        clock=app_state.clock,
        receiver=app_state.receiver,
        issuer=app_state.issuer,
        release_verifier=app_state.release_verifier,
        revocation=app_state.revocation,
        ledger=app_state.ledger,
        verifier=app_state.verifier,
        operations=app_state.operations,
    )
    result = SCENARIOS[scenario_id](world)
    result["scenario_id"] = scenario_id
    if not result.get("description"):
        result["description"] = _DESCRIPTIONS.get(scenario_id, "")
    return result