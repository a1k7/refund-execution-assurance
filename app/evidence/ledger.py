"""Append-only, hash-chained evidence ledger.

Writer side: ``append(event)`` canonicalizes and stores one row.
Reader side: ``read_all``, ``read_for_operation``, ``verify_chain``.

The ledger uses ``BEGIN IMMEDIATE`` for the read-then-write of ``last_hash`` and
``append`` so two concurrent appends cannot interleave a bad chain. It is safe
for the single-process architecture documented in ARCHITECTURE.md.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..domain.enums import EvidenceEventType
from ..domain.models import EvidenceEvent
from ..persistence.db import Database
from ..persistence.repositories import EvidenceRepository
from .canonical import GENESIS_PREV_HASH, compute_event_hash
from .schema import from_storage_payload, to_storage_payload


class EvidenceLedger:
    def __init__(self, db: Database, repository: Optional[EvidenceRepository] = None) -> None:
        self.db = db
        self.repo = repository or EvidenceRepository(db)

    # --- writer ---------------------------------------------------------

    def append(self, event: EvidenceEvent) -> int:
        """Append an event atomically. Returns the sequence number."""
        with self.db.transaction() as c:
            row = c.execute("SELECT hash FROM evidence ORDER BY sequence DESC LIMIT 1").fetchone()
            prev_hash = row["hash"] if row is not None else GENESIS_PREV_HASH
            payload = to_storage_payload(event)
            event_hash = compute_event_hash(prev_hash, payload)
            cur = c.execute(
                """
                INSERT INTO evidence (event_id, event_type, business_operation_id, event_json, prev_hash, hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type.value,
                    event.business_operation_id,
                    json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                    prev_hash,
                    event_hash,
                    event.timestamp.isoformat(),
                ),
            )
            return int(cur.lastrowid)

    # --- reader ---------------------------------------------------------

    def read_all(self) -> List[Dict[str, Any]]:
        rows = self.repo.list_all()
        return [self._hydrate(r) for r in rows]

    def read_for_operation(self, business_operation_id: str) -> List[Dict[str, Any]]:
        rows = self.repo.list_for_operation(business_operation_id)
        return [self._hydrate(r) for r in rows]

    def verify_chain(self) -> bool:
        rows = self.repo.list_all()
        prev = GENESIS_PREV_HASH
        for row in rows:
            payload = json.loads(row["event_json"])
            expected = compute_event_hash(prev, payload)
            if expected != row["hash"] or row["prev_hash"] != prev:
                return False
            prev = row["hash"]
        return True

    def last_hash(self) -> Optional[str]:
        row = self.repo.last()
        return row["hash"] if row else None

    def _hydrate(self, row: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.loads(row["event_json"])
        out = dict(payload)
        out["_sequence"] = row["sequence"]
        out["_prev_hash"] = row["prev_hash"]
        out["_hash"] = row["hash"]
        return out


def event_from_row(row: Dict[str, Any]) -> EvidenceEvent:
    payload = {k: v for k, v in row.items() if not k.startswith("_")}
    return from_storage_payload(payload)


__all__ = ["EvidenceLedger", "event_from_row"]