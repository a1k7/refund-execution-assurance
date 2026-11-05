"""Scenario 12 — Expired, tampered, wrong-audience, wrong-capability, invalid signature."""
from __future__ import annotations

import base64

from app.domain.enums import OperationState


def _issue(world, bo):
    return world.issuer.issue(
        business_operation_id=bo,
        release_epoch=world.operations.get(bo).current_epoch,  # type: ignore[union-attr]
        subject="agent-test",
        audience="decisionassure:refunds",
        capability="refund.issue",
        policy_version="v1",
        amount=1000,
        currency="USD",
        params_digest="sha256:test",
    )


def test_expired_release_rejected(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    world.clock.advance(400)  # default validity is 300 s
    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    assert "expired" in (result.reason or "")
    assert world.oracle.refund_count() == 0


def test_tampered_amount_breaks_signature(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    signed.payload.amount = 999_999
    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    assert "invalid_signature" in (result.reason or "")


def test_invalid_signature_bytes_rejected(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    signed.signature.signature_b64 = base64.b64encode(b"\x00" * 64).decode("ascii")
    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    assert "invalid_signature" in (result.reason or "")


def test_wrong_audience_rejected(world) -> None:
    """Signed with the wrong audience, signature is valid, audience check fails."""
    bo = world.create_operation()
    signed = _issue(world, bo)
    # Re-sign is not needed; the audience was baked in *before* signing only if we
    # issue with the wrong audience. Here we issue correctly then tamper — so the
    # signature breaks first. Test the *signed-correctly-with-wrong-audience* case:
    signed2 = world.issuer.issue(
        business_operation_id=bo,
        release_epoch=0,
        subject="agent-test",
        audience="other-audience",  # wrong, but signed as-is
        capability="refund.issue",
        policy_version="v1",
        amount=1000,
        currency="USD",
        params_digest="sha256:test",
    )
    from app.persistence.repositories import ReleaseRepository
    ReleaseRepository(world.db).insert(signed2, world.clock.now())
    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed2, idempotency_key="idem_1"
    )
    assert "wrong_audience" in (result.reason or "")


def test_wrong_capability_rejected(world) -> None:
    bo = world.create_operation()
    signed = world.issuer.issue(
        business_operation_id=bo,
        release_epoch=0,
        subject="agent-test",
        audience="decisionassure:refunds",
        capability="refund.approve",  # wrong, but signed as-is
        policy_version="v1",
        amount=1000,
        currency="USD",
        params_digest="sha256:test",
    )
    from app.persistence.repositories import ReleaseRepository
    ReleaseRepository(world.db).insert(signed, world.clock.now())
    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    assert "wrong_capability" in (result.reason or "")


def test_no_receiver_call_for_any_invalid_release(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)
    signed.payload.amount = 1
    world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    assert world.oracle.refund_count() == 0
    op = world.operations.get(bo)
    assert op.state is OperationState.RELEASE_ISSUED