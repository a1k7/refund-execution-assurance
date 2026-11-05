"""Simulated Parmana release issuer.

Stands in for Parmana's signed-release issuance. It uses Ed25519 with a
locally stored PEM private key.

HONESTY:
  * This is NOT production key management. Keys are on the local filesystem.
  * The trust store is populated by the issuer on first use for developer
    convenience. In production the trust store is provisioned out-of-band.
  * The canonical signing form is a local convention; see ``canonical.py``.

The single-use property of the release is enforced by DecisionAssure's
local consumption, not by this issuer. See connector/atomic_consumption.py.
"""
from __future__ import annotations

import base64
from datetime import timedelta
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..clock import Clock
from ..domain.ids import new_release_id
from ..domain.models import ReleasePayload, ReleaseSignature, SignedRelease
from .canonical import canonical_bytes
from .trust_store import TrustStore


class ParmanaReleaseIssuer:
    def __init__(
        self,
        key_id: str,
        private_key_path: str,
        trust_store: TrustStore,
        clock: Clock,
    ) -> None:
        self.key_id = key_id
        self.private_key_path = Path(private_key_path)
        self.trust_store = trust_store
        self.clock = clock
        self._private_key: Optional[Ed25519PrivateKey] = None

    def _load_or_create(self) -> Ed25519PrivateKey:
        if self._private_key is not None:
            return self._private_key
        if self.private_key_path.exists():
            raw = self.private_key_path.read_bytes()
            loaded = serialization.load_pem_private_key(raw, password=None)
            assert isinstance(loaded, Ed25519PrivateKey), "expected Ed25519 private key"
            self._private_key = loaded
        else:
            key = Ed25519PrivateKey.generate()
            self.private_key_path.parent.mkdir(parents=True, exist_ok=True)
            pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            self.private_key_path.write_bytes(pem)
            self._private_key = key
        # Ensure the public key is present in the trust store.
        self.trust_store.add(self.key_id, self._private_key.public_key())
        return self._private_key

    def issue(
        self,
        *,
        business_operation_id: str,
        release_epoch: int,
        subject: str,
        audience: str,
        capability: str,
        policy_version: str,
        amount: int,
        currency: str,
        params_digest: str,
        valid_for_seconds: int = 300,
        release_id: Optional[str] = None,
    ) -> SignedRelease:
        key = self._load_or_create()
        now = self.clock.now()
        payload = ReleasePayload(
            release_id=release_id or new_release_id(),
            release_epoch=release_epoch,
            business_operation_id=business_operation_id,
            subject=subject,
            audience=audience,
            capability=capability,
            policy_version=policy_version,
            issued_at=now,
            expires_at=now + timedelta(seconds=valid_for_seconds),
            amount=amount,
            currency=currency,
            params_digest=params_digest,
        )
        canonical = canonical_bytes(payload.model_dump(mode="json"))
        sig_bytes = key.sign(canonical)
        return SignedRelease(
            payload=payload,
            signature=ReleaseSignature(
                key_id=self.key_id,
                signature_b64=base64.b64encode(sig_bytes).decode("ascii"),
            ),
        )