"""Offline verifier CLI.

Usage:
    python -m cli.verify <db_path> <business_operation_id>
    python -m cli.verify <db_path> --ledger

Runs without starting the API, without importing the receiver, and without
touching any live process. Reads from the SQLite database at ``db_path``.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.evidence.ledger import EvidenceLedger
from app.persistence.db import Database
from app.verification.verifier import IndependentVerifier


def main() -> int:
    parser = argparse.ArgumentParser(prog="cli.verify")
    parser.add_argument("db_path", help="Path to the SQLite database")
    parser.add_argument("business_operation_id", nargs="?", help="Business operation ID")
    parser.add_argument("--ledger", action="store_true", help="Verify the full evidence ledger")
    args = parser.parse_args()

    db = Database(args.db_path)
    ledger = EvidenceLedger(db)
    verifier = IndependentVerifier(ledger)

    if args.ledger:
        ok, count = verifier.verify_ledger()
        print(json.dumps({"chain_valid": ok, "event_count": count}, indent=2))
        return 0 if ok else 1

    if not args.business_operation_id:
        parser.error("business_operation_id is required unless --ledger is used")

    report = verifier.verify(args.business_operation_id)
    print(report.model_dump_json(indent=2))
    return 0 if report.verification_status.value != "FAILED" else 1


if __name__ == "__main__":
    sys.exit(main())