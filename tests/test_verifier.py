"""Independent verifier: evidence-only reasoning."""
from __future__ import annotations

from app.domain.enums import (
    EvidenceEventType,
    OperationState,
    ReceiverResponseClass,
    TerminalClassification,
    VerificationStatus,
)
from app.evidence.schema import build_event
from app.receiver.simulated import SimulatedReceiverBehaviour


def test_no_evidence_yields_inconclusive(world) -> None:
    bo = world.create_operation()
    # Clear the OPERATION_CREATED event we made for a truly empty ledger.
    with world.db.transaction() as c:
        c.execute("DELETE FROM evidence WHERE business_operation_id = ?", (bo,))
    report = world.verifier.verify(bo)
    assert report.verification_status is VerificationStatus.INCONCLUSIVE
    assert "no_evidence_events" in report.failed_assertions
    assert report.reconciliation_required is True


def test_completion_with_terminal_evidence_verifies(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(complete_on_accept=True))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        # Now query status to record terminal completion evidence.
        world.cancellation.confirm_from_status(bo)
        report = world.verifier.verify(bo)
        assert report.verification_status is VerificationStatus.VERIFIED
        assert report.terminal_classification is TerminalClassification.COMPLETION_CONFIRMED
        assert report.retry_eligible is False
        assert report.reconciliation_required is False
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_cancellation_with_terminal_evidence_verifies(world) -> None:
    from app.domain.enums import InFlightPolicy
    bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
    signed = world.issue_release(bo)
    world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_1"
    )
    world.cancellation.request_cancellation(bo)
    report = world.verifier.verify(bo)
    assert report.terminal_classification is TerminalClassification.CANCELLATION_CONFIRMED
    assert report.verification_status is VerificationStatus.VERIFIED


def test_outcome_unknown_is_not_treated_as_terminal_success(world) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        report = world.verifier.verify(bo)
        assert report.verification_status is VerificationStatus.INCONCLUSIVE
        assert report.terminal_classification is TerminalClassification.OUTCOME_UNKNOWN
        assert report.retry_eligible is False
        assert report.reconciliation_required is True
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_completion_confirmed_without_terminal_class_fails(world) -> None:
    """A COMPLETION_CONFIRMED event lacking STATUS_TERMINAL_COMPLETED is a verification failure."""
    bo = world.create_operation()
    world.ledger.append(build_event(
        event_type=EvidenceEventType.COMPLETION_CONFIRMED,
        evidence_source="test",
        timestamp=world.clock.now(),
        business_operation_id=bo,
        previous_state=OperationState.IN_FLIGHT,
        new_state=OperationState.COMPLETION_CONFIRMED,
        receiver_response_class=None,  # deliberately omitted
    ))
    report = world.verifier.verify(bo)
    assert report.verification_status is VerificationStatus.FAILED
    assert any("completion_confirmed_without_terminal_receiver_evidence" in f for f in report.failed_assertions)


def test_cancellation_confirmed_without_terminal_class_fails(world) -> None:
    bo = world.create_operation()
    world.ledger.append(build_event(
        event_type=EvidenceEventType.CANCELLATION_CONFIRMED,
        evidence_source="test",
        timestamp=world.clock.now(),
        business_operation_id=bo,
        previous_state=OperationState.CANCELLATION_ACKNOWLEDGED,
        new_state=OperationState.CANCELLATION_CONFIRMED,
        receiver_response_class=None,
    ))
    report = world.verifier.verify(bo)
    assert report.verification_status is VerificationStatus.FAILED
    assert any("cancellation_confirmed_without_terminal_receiver_evidence" in f for f in report.failed_assertions)


def test_tampered_ledger_causes_failure(world) -> None:
    bo = world.create_operation()
    world.ledger.append(build_event(
        event_type=EvidenceEventType.DISPATCH_ATTEMPTED,
        evidence_source="test",
        timestamp=world.clock.now(),
        business_operation_id=bo,
        previous_state=OperationState.RELEASE_CONSUMED,
        new_state=OperationState.DISPATCH_ATTEMPTED,
    ))
    # Tamper event_json
    with world.db.transaction() as c:
        c.execute(
            "UPDATE evidence SET event_json = '{\"tampered\":true}' WHERE business_operation_id = ?",
            (bo,),
        )
    report = world.verifier.verify(bo)
    assert report.verification_status is VerificationStatus.FAILED
    assert "chain_invalid" in report.failed_assertions


def test_verify_ledger_returns_tuple(world) -> None:
    ok, count = world.verifier.verify_ledger()
    assert ok is True
    assert count == len(world.ledger.read_all())


def test_forged_dispatch_rejected_fails(world) -> None:
    """A forged DISPATCH_REJECTED without a valid response class must FAIL verification."""
    bo = world.create_operation()
    world.ledger.append(build_event(
        event_type=EvidenceEventType.DISPATCH_REJECTED,
        evidence_source="test",
        timestamp=world.clock.now(),
        business_operation_id=bo,
        previous_state=OperationState.DISPATCH_ATTEMPTED,
        new_state=OperationState.DISPATCH_REJECTED,
        receiver_response_class=None,  # Forged / incomplete
    ))
    report = world.verifier.verify(bo)
    assert report.verification_status is VerificationStatus.FAILED
    assert any("dispatch_rejected_without_valid_receiver_rejection_evidence" in f for f in report.failed_assertions)
    assert any("dispatch_rejected_has_valid_response" in f for f in report.failed_assertions)


def test_admissibility_denied_is_verified_as_rejected(world) -> None:
    """ADMISSIBILITY_DENIED is a safe local rejection; verifier must classify it as REJECTED_NO_FUNDS."""
    bo = world.create_operation()
    world.ledger.append(build_event(
        event_type=EvidenceEventType.ADMISSIBILITY_DENIED,
        evidence_source="test",
        timestamp=world.clock.now(),
        business_operation_id=bo,
        previous_state=OperationState.RELEASE_VERIFIED,
        new_state=OperationState.ADMISSIBILITY_DENIED,
        receiver_response_class=None,
    ))
    report = world.verifier.verify(bo)
    assert report.verification_status is VerificationStatus.VERIFIED
    assert report.terminal_classification is TerminalClassification.REJECTED_NO_FUNDS
    assert report.retry_eligible is True