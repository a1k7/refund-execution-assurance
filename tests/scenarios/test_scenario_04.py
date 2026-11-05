"""Scenario 4 — Revocation after dispatch; cancellation confirmed via status query."""
from __future__ import annotations

from app.domain.enums import (
    InFlightPolicy,
    OperationState,
    TerminalClassification,
    VerificationStatus,
)


def test_revocation_after_dispatch_cancellation_confirmed(world) -> None:
    bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
    signed = world.issue_release(bo)
    dispatch = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    assert dispatch.operation_state is OperationState.IN_FLIGHT
    ref = dispatch.receiver_refund_reference
    assert ref is not None

    world.revocation.revoke(bo, source="scenario-04", receiver=world.receiver)

    final_state = world.cancellation.request_cancellation(bo)
    assert final_state is OperationState.CANCELLATION_CONFIRMED

    op = world.operations.get(bo)
    assert op is not None
    assert op.terminal_classification is TerminalClassification.CANCELLATION_CONFIRMED
    assert op.retry_eligible is False

    report = world.verifier.verify(bo)
    assert report.verification_status is VerificationStatus.VERIFIED
    assert report.terminal_classification is TerminalClassification.CANCELLATION_CONFIRMED

    refund = world.oracle.refund_record(ref)
    assert refund.state.value == "CANCELLED"