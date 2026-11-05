"""Canonical JSON for signing and verifying Parmana releases.

This module is the single source of truth for the byte-level representation
that gets signed. If Parmana's real spec uses a different canonical form
(e.g. JCS, protobuf, or a specific field ordering), this is the ONE place
that needs to change. Everything else in the system depends only on
``canonical_bytes()``.

Rules:
  * sort_keys=True
  * separators=(",", ":")  -> no whitespace
  * default=str            -> datetimes are isoformat (must be tz-aware)
  * UTF-8 encoding

Assumption A-1 (see INTEGRATION_CONTRACT.md): real Parmana canonical form
is TBD; this is the local stand-in used by the simulated issuer and verifier.
"""
from __future__ import annotations

import json
from typing import Any, Dict


def canonical_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")