"""Release verification: cryptographic + policy checks."""
from __future__ import annotations

import base64

import pytest

from app.domain.errors import ReleaseVerificationError


def _issue_valid(world, **overrides):
    return world.issuer.issue(
        business_operation_id=overrides.pop("business_operation_id", "bo_verify"),
        release_epoch=overrides.pop("release_epoch", 0),
        subject="agent",
        audience=overrides.pop("audience", "decisionassure:refunds"),
        capability=overrides.pop("capability", "refund.issue"),
        policy_version="v1",
        amount=overrides.pop("amount", 1000),
        currency="USD",
        params_digest="sha256:test",
        **overrides,
    )


def test_valid_release_passes(world) -> None:
    signed = _issue_valid(world)
    # Should not raise.
    world.release_verifier.verify(signed, now=world.clock.now())


def test_expired_release_is_rejected(world) -> None:
    signed = _issue_valid(world)
    world.clock.advance(400)  # default validity is 300s
    with pytest.raises(ReleaseVerificationError, match="expired"):
        world.release_verifier.verify(signed, now=world.clock.now())


def test_release_before_issue_time_is_rejected(world) -> None:
    from datetime import timedelta
    signed = _issue_valid(world)
    earlier = world.clock.now() - timedelta(seconds=60)
    with pytest.raises(ReleaseVerificationError, match="not_yet_valid"):
        world.release_verifier.verify(signed, now=earlier)


def test_tampered_amount_breaks_signature(world) -> None:
    signed = _issue_valid(world)
    signed.payload.amount = 999_999
    with pytest.raises(ReleaseVerificationError, match="invalid_signature"):
        world.release_verifier.verify(signed, now=world.clock.now())


def test_tampered_audience_breaks_signature(world) -> None:
    """Changing audience post-signing invalidates the signature before audience check."""
    signed = _issue_valid(world)
    signed.payload.audience = "someone-else"
    with pytest.raises(ReleaseVerificationError, match="invalid_signature"):
        world.release_verifier.verify(signed, now=world.clock.now())


def test_wrong_audience_at_issuance_fails_audience_check(world) -> None:
    """Signed with wrong audience: signature valid, audience check must fail."""
    signed = _issue_valid(world, audience="other-audience")
    with pytest.raises(ReleaseVerificationError, match="wrong_audience"):
        world.release_verifier.verify(signed, now=world.clock.now())


def test_wrong_capability_at_issuance_fails_capability_check(world) -> None:
    signed = _issue_valid(world, capability="refund.approve")
    with pytest.raises(ReleaseVerificationError, match="wrong_capability"):
        world.release_verifier.verify(signed, now=world.clock.now())


def test_unknown_key_id_is_rejected(world) -> None:
    signed = _issue_valid(world)
    signed.signature.key_id = "unknown-key-42"
    with pytest.raises(ReleaseVerificationError, match="unknown_key_id"):
        world.release_verifier.verify(signed, now=world.clock.now())


def test_invalid_signature_encoding_is_rejected(world) -> None:
    signed = _issue_valid(world)
    signed.signature.signature_b64 = "!!! not base64 !!!"
    with pytest.raises(ReleaseVerificationError, match="invalid_signature_encoding"):
        world.release_verifier.verify(signed, now=world.clock.now())


def test_wrong_signature_bytes_are_rejected(world) -> None:
    signed = _issue_valid(world)
    signed.signature.signature_b64 = base64.b64encode(b"\x00" * 64).decode("ascii")
    with pytest.raises(ReleaseVerificationError, match="invalid_signature"):
        world.release_verifier.verify(signed, now=world.clock.now())


def test_expected_business_operation_id_mismatch_is_rejected(world) -> None:
    signed = _issue_valid(world, business_operation_id="bo_AA")
    with pytest.raises(ReleaseVerificationError, match="business_operation_mismatch"):
        world.release_verifier.verify(
            signed, now=world.clock.now(), expected_business_operation_id="bo_BB"
        )