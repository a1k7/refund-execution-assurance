"""Run a single demo scenario from the terminal (no API needed)."""
from __future__ import annotations

import argparse
import json

from app.demo.runner import list_scenarios, run_scenario
from app.main import build_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario_id", nargs="?", help="e.g. 01")
    parser.add_argument("--list", action="store_true", help="list scenarios")
    args = parser.parse_args()

    if args.list:
        for s in list_scenarios():
            print(f"{s['scenario_id']}  {s['description']}")
        return

    if not args.scenario_id:
        parser.error("scenario_id required (or use --list)")

    app = build_app()
    result = run_scenario(app.state.app_state, args.scenario_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()