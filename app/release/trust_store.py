"""Trust store: maps ``key_id`` -> Ed25519 public key.

In this reference implementation the trust store is a JSON file:

    {"parmana-demo-key-1": "<base64(raw-32-bytes)>", ...}

This is intentionally simple. Real Parmana deployments would use an
HSM/remote KMS with key rotation, provenance, and revocation; the JSON
file is documented as a limitation in THREAT_MODEL.md and INTEGRATION_CONTRACT.md.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Dict, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


class TrustStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._cache: Dict[str, Ed25519PublicKey] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for key_id, raw_b64 in data.items():
            raw = base64.b64decode(raw_b64)
            self._cache[key_id] = Ed25519PublicKey.from_public_bytes(raw)

    def reload(self) -> None:
        self._cache.clear()
        self._load()

    def add(self, key_id: str, public_key: Ed25519PublicKey) -> None:
        self._cache[key_id] = public_key
        self._persist()

    def get(self, key_id: str) -> Optional[Ed25519PublicKey]:
        return self._cache.get(key_id)

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            k: base64.b64encode(v.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
            for k, v in self._cache.items()
        }
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")