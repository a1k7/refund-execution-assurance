"""Simulated Parmana issuer: signatures, canonical form, key handling."""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.release.canonical import canonical_bytes


def _issue(world, **overrides):
    args = dict(
        business_operation_id="bo_test",
        release_epoch=0,
        subject="agent",
        audience="decisionassure:refunds",
        capability="refund.issue",
        policy_version="v1",
        amount=1000,
        currency="USD",
        params_digest="sha256:test",
    )
    args.update(overrides)
    return world.issuer.issue(**args)


def test_issue_creates_well_formed_release(world) -> None:
    signed = _issue(world)
    p = signed.payload
    assert p.release_id.startswith("rel_")
    assert p.release_epoch == 0
    assert p.business_operation_id == "bo_test"
    assert p.subject == "agent"
    assert p.audience == "decisionassure:refunds"
    assert p.capability == "refund.issue"
    assert p.amount == 1000
    assert p.currency == "USD"
    assert p.issued_at < p.expires_at
    assert signed.signature.alg == "Ed25519"
    assert signed.signature.key_id == "test-key"


def test_signature_is_valid_ed25519_over_canonical_bytes(world) -> None:
    signed = _issue(world)
    pub = world.trust_store.get(signed.signature.key_id)
    assert pub is not None
    sig_bytes = base64.b64decode(signed.signature.signature_b64)
    canonical = canonical_bytes(signed.payload.model_dump(mode="json"))
    # Should not raise.
    pub.verify(sig_bytes, canonical)


def test_canonical_form_is_stable_and_whitespace_free(world) -> None:
    signed = _issue(world)
    payload_dict = signed.payload.model_dump(mode="json")
    b1 = canonical_bytes(payload_dict)
    b2 = canonical_bytes(payload_dict)
    assert b1 == b2
    assert b" " not in b1
    assert b"\n" not in b1
    # sorted keys => "amount" appears before "audience"
    assert b1.index(b"amount") < b1.index(b"audience")


def test_each_issue_generates_a_unique_release_id(world) -> None:
    ids = {_issue(world).payload.release_id for _ in range(5)}
    assert len(ids) == 5


def test_different_payloads_produce_different_signatures(world) -> None:
    a = _issue(world, amount=1000)
    b = _issue(world, amount=2000)
    assert a.signature.signature_b64 != b.signature.signature_b64


def test_trust_store_persists_public_key(world) -> None:
    _issue(world)  # populates trust store
    path = world.trust_store.path
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "test-key" in text


def test_private_key_is_persisted_and_reusable(world) -> None:
    _issue(world)
    path = world.tmp_path / "parmana_private.pem"
    assert path.exists()
    assert b"BEGIN PRIVATE KEY" in path.read_bytes()


def test_issuer_rejects_unsupported_private_key_format(tmp_path) -> None:
    """Loading a non-PEM file must fail loudly rather than silently regenerating."""
    import pytest
    from app.release.issuer import ParmanaReleaseIssuer
    from app.release.trust_store import TrustStore

    bad = tmp_path / "bad.pem"
    bad.write_bytes(b"not-a-pem")
    trust = TrustStore(str(tmp_path / "trust.json"))
    issuer = ParmanaReleaseIssuer(
        key_id="test-key", private_key_path=str(bad), trust_store=trust, clock=None,  # type: ignore[arg-type]
    )
    with pytest.raises(Exception):
        issuer.issue(
            business_operation_id="bo", release_epoch=0, subject="a",
            audience="decisionassure:refunds", capability="refund.issue",
            policy_version="v1", amount=1, currency="USD", params_digest="d",
        )