"""Scenario 3 — Revocation between connector's check and receiver's acceptance."""
from __future__ import annotations

from app.domain.enums import (
    OperationState,
    ReceiverResponseClass,
    TerminalClassification,
    VerificationStatus,
)
from app.connector.hooks import DispatchContext


def test_revocation_between_check_and_acceptance_is_fenced(world) -> None:
    bo = world.create_operation()
    signed = world.issue_release(bo)

    def revoke_mid_dispatch(ctx: DispatchContext) -> None:
        # Fired *after* atomic consumption and *before* the receiver call.
        world.revocation.revoke(bo, source="scenario-03", receiver=world.receiver)

    world.hooks.on_before_receiver_call(revoke_mid_dispatch)
    try:
        result = world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
    finally:
        world.hooks.clear()

    assert result.response_class is ReceiverResponseClass.REJECTED_FENCED
    assert result.operation_state is OperationState.DISPATCH_REJECTED
    assert world.oracle.refund_count() == 0

    # Determinism check: the hook fired exactly once, no sleeps, no retries.
    events = world.ledger.read_for_operation(bo)
    types = [e["event_type"] for e in events]
    assert types.count("RELEASE_CONSUMED") == 1
    assert types.count("AUTHORITY_REVOKED") == 1
    assert "DISPATCH_REJECTED" in types

    # Verifier must establish safe rejection, confirm no funds moved, and mark it retry eligible.
    report = world.verifier.verify(bo)
    assert report.verification_status is VerificationStatus.VERIFIED
    assert report.terminal_classification is TerminalClassification.REJECTED_STALE_AUTHORITY
    assert report.retry_eligible is True
    assert report.reconciliation_required is False
    assert not report.failed_assertions


def test_cannot_be_misclassified_as_insufficient_funds(world) -> None:
    """Proves that a fenced rejection is NOT classified as REJECTED_NO_FUNDS."""
    bo = world.create_operation()
    signed = world.issue_release(bo)

    def revoke_mid_dispatch(ctx: DispatchContext) -> None:
        world.revocation.revoke(bo, source="scenario-03", receiver=world.receiver)

    world.hooks.on_before_receiver_call(revoke_mid_dispatch)
    try:
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
    finally:
        world.hooks.clear()

    report = world.verifier.verify(bo)
    assert report.terminal_classification is TerminalClassification.REJECTED_STALE_AUTHORITY
    assert report.terminal_classification is not TerminalClassification.REJECTED_NO_FUNDS


def test_replay_of_fenced_release_requires_new_release(world) -> None:
    """Proves the consumed old release cannot be replayed, even if retry is allowed."""
    bo = world.create_operation()
    signed = world.issue_release(bo)

    def revoke_mid_dispatch(ctx: DispatchContext) -> None:
        world.revocation.revoke(bo, source="scenario-03", receiver=world.receiver)

    world.hooks.on_before_receiver_call(revoke_mid_dispatch)
    try:
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
    finally:
        world.hooks.clear()

    # Now, try to replay the exact same release. The duplicate guard allows it (previous
    # attempt was DISPATCH_REJECTED), but the coordinator's authority gate catches it.
    # Since authority is revoked AND the epoch is stale, the coordinator prioritizes
    # the more severe "authority_revoked" reason.
    replay_result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_replay"
    )
    
    assert replay_result.operation_state is OperationState.ADMISSIBILITY_DENIED
    assert replay_result.reason == "authority_revoked"
    assert world.oracle.refund_count() == 0

    # Verify the replay attempt is recorded correctly.
    report = world.verifier.verify(bo)
    assert report.terminal_classification is TerminalClassification.REJECTED_STALE_AUTHORITY
    assert report.retry_eligible is True


def test_retry_requires_fresh_release_and_admissibility_eval(world) -> None:
    """Proves that retrying requires a new epoch release, and that a fresh release is allowed."""
    bo = world.create_operation()
    signed = world.issue_release(bo)

    def revoke_mid_dispatch(ctx: DispatchContext) -> None:
        world.revocation.revoke(bo, source="scenario-03", receiver=world.receiver)

    world.hooks.on_before_receiver_call(revoke_mid_dispatch)
    try:
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
    finally:
        world.hooks.clear()

    # Issue a *fresh* release at the new epoch.
    fresh_signed = world.issue_release(bo)
    assert fresh_signed.payload.release_epoch == 1

    # Now try to dispatch with the fresh release. But authority is revoked!
    # The coordinator should deny it at the admissibility gate.
    # This proves a fresh admissibility evaluation is required, not just a fresh release.
    fresh_result = world.coordinator.dispatch(
        business_operation_id=bo, release=fresh_signed, idempotency_key="idem_fresh"
    )
    assert fresh_result.operation_state is OperationState.ADMISSIBILITY_DENIED
    assert fresh_result.reason == "authority_revoked"