"""Release verifier (cryptographic + policy).

This module is deliberately *pure*: given a ``SignedRelease`` and a datetime,
it either returns None (valid) or raises ``ReleaseVerificationError``.

It does NOT:
  * Check single-use (that is the atomic consumption step).
  * Check authority/revocation state (that is the dispatch coordinator).
  * Touch the DB or the receiver.
  * Record evidence (the coordinator records outcomes, including failures).

This separation keeps the verifier trivially unit-testable and makes the
whole admissibility pipeline easy to reason about.
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Optional

from cryptography.exceptions import InvalidSignature

from ..domain.errors import ReleaseVerificationError
from ..domain.models import SignedRelease
from .canonical import canonical_bytes
from .trust_store import TrustStore


class ReleaseVerifier:
    def __init__(
        self,
        trust_store: TrustStore,
        expected_audience: str,
        expected_capability: str,
    ) -> None:
        self.trust_store = trust_store
        self.expected_audience = expected_audience
        self.expected_capability = expected_capability

    def verify(
        self,
        release: SignedRelease,
        *,
        now: datetime,
        expected_business_operation_id: Optional[str] = None,
    ) -> None:
        payload = release.payload

        # 1. Signature
        pub = self.trust_store.get(release.signature.key_id)
        if pub is None:
            raise ReleaseVerificationError(f"unknown_key_id:{release.signature.key_id}")
        try:
            sig = base64.b64decode(release.signature.signature_b64, validate=True)
        except Exception as exc:
            raise ReleaseVerificationError(f"invalid_signature_encoding:{exc}") from exc
        canonical = canonical_bytes(payload.model_dump(mode="json"))
        try:
            pub.verify(sig, canonical)
        except InvalidSignature as exc:
            raise ReleaseVerificationError("invalid_signature") from exc

        # 2. Audience / capability
        if payload.audience != self.expected_audience:
            raise ReleaseVerificationError(f"wrong_audience:{payload.audience}")
        if payload.capability != self.expected_capability:
            raise ReleaseVerificationError(f"wrong_capability:{payload.capability}")

        # 3. Validity window
        if now < payload.issued_at:
            raise ReleaseVerificationError("not_yet_valid")
        if now >= payload.expires_at:
            raise ReleaseVerificationError("expired")

        # 4. Optional business op binding
        if (
            expected_business_operation_id is not None
            and payload.business_operation_id != expected_business_operation_id
        ):
            raise ReleaseVerificationError(
                f"business_operation_mismatch:{payload.business_operation_id}"
            )