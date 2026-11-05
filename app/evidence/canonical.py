"""Deterministic canonicalization for evidence hashing.

The canonical form is:
    sha256( prev_hash_bytes || b"|" || json.dumps(payload, sort_keys=True, separators=(",", ":")) )

The first event uses prev_hash == "" (empty string) — this is a documented constant,
not a hash. Ordering is by ``sequence`` (AUTOINCREMENT). The chain proves internal
consistency; it does NOT protect against full database compromise (an attacker who
can rewrite every row and recompute every hash will produce a valid chain). See
EVIDENCE_SPEC.md §4 "What the hash chain proves".
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

GENESIS_PREV_HASH = ""


def canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_event_hash(prev_hash: str, payload: Dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"|")
    h.update(canonical_json(payload).encode("utf-8"))
    return h.hexdigest()