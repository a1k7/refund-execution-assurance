"""Demo scenario runner endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..demo.runner import list_scenarios, run_scenario
from .deps import AppState, get_app_state
from .schemas import RunScenarioRequest, ScenarioResultView

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/scenarios")
def scenarios() -> list[dict]:
    return list_scenarios()


@router.post("/run", response_model=ScenarioResultView)
def run(body: RunScenarioRequest, state: AppState = Depends(get_app_state)) -> ScenarioResultView:
    try:
        result = run_scenario(state, body.scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown_scenario:{exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"scenario_error:{type(exc).__name__}:{exc}") from exc
    return ScenarioResultView(**result)