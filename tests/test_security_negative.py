"""Security negative tests.

These are the "adversarial" tests. They include a source-grep check to
ensure the verifier stays decoupled from the receiver, and behavioural
tests that prove the verifier cannot infer hidden receiver state.
"""
from __future__ import annotations

import pathlib
import re

from app.domain.enums import OperationState, ReceiverResponseClass, TerminalClassification
from app.receiver.simulated import SimulatedReceiverBehaviour


_APP_DIR = pathlib.Path(__file__).parent.parent / "app"


def test_app_does_not_reference_receiver_oracle() -> None:
    bad_files: list[str] = []
    for py in _APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "receiver_oracle" in text or "ReceiverOracle" in text:
            bad_files.append(str(py))
    assert not bad_files, f"app/ references the test-only oracle: {bad_files}"


def test_verifier_module_has_no_imports_from_receiver_package() -> None:
    verifier_path = _APP_DIR / "verification" / "verifier.py"
    text = verifier_path.read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", text, re.MULTILINE)
    bad = [imp for imp in imports if "receiver" in imp]
    assert not bad, f"verifier.py imports receiver: {bad}"


def test_verifier_does_not_access_private_receiver_attributes() -> None:
    """No app/ file may reference the simulated receiver's private state."""
    bad: list[str] = []
    for py in _APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # The simulated receiver is allowed to reference _refunds (its own state).
        if py.name == "simulated.py":
            continue
        if "._refunds" in text or "._fencing" in text or "._by_idempotency" in text:
            bad.append(str(py))
    assert not bad, f"app/ references receiver private state: {bad}"


def test_verifier_cannot_see_hidden_completion(world) -> None:
    """Receiver internally completes but reports inconclusive.
    Verifier must return OUTCOME_UNKNOWN (not COMPLETION_CONFIRMED)."""
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        complete_on_accept=True,
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = world.create_operation()
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        # Ground truth: completed.
        assert world.oracle.refund_count() == 1
        rec = list(world.oracle.all_refunds().values())[0]
        assert rec.state.value == "COMPLETED"
        # Verifier's view: OUTCOME_UNKNOWN.
        report = world.verifier.verify(bo)
        assert report.terminal_classification is TerminalClassification.OUTCOME_UNKNOWN
        assert report.retry_eligible is False
        assert report.reconciliation_required is True
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_cancellation_ack_alone_is_not_confirmed(world) -> None:
    """CANCEL_ACK followed by PENDING must never be classified as CANCELLATION_CONFIRMED."""
    from app.domain.enums import InFlightPolicy
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        cancel_transitions_to_terminal=False,
        status_class_override=ReceiverResponseClass.STATUS_PENDING,
    ))
    try:
        bo = world.create_operation(policy=InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = world.issue_release(bo)
        world.coordinator.dispatch(
            business_operation_id=bo, release=signed, idempotency_key="idem_1"
        )
        result_state = world.cancellation.request_cancellation(bo)
        assert result_state is OperationState.OUTCOME_UNKNOWN
        op = world.operations.get(bo)
        assert op is not None
        assert op.terminal_classification is None or op.terminal_classification.value == "OUTCOME_UNKNOWN"
    finally:
        world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def test_different_idempotency_key_does_not_create_new_dispatch_slot(world) -> None:
    """Two dispatches with the same release but different idempotency keys:
    the second is rejected (duplicate guard or release_already_consumed)."""
    bo = world.create_operation()
    signed = world.issue_release(bo)
    first = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_A"
    )
    assert first.operation_state is OperationState.IN_FLIGHT
    second = world.coordinator.dispatch(
        business_operation_id=bo, release=signed, idempotency_key="idem_B"
    )
    assert "duplicate_guard" in (second.reason or "")


def test_stale_epoch_release_rejected_by_connector(world) -> None:
    bo = world.create_operation()
    signed_old = world.issue_release(bo, release_epoch=0)
    world.revocation.supersede(bo, source="test", receiver=world.receiver)
    result = world.coordinator.dispatch(
        business_operation_id=bo, release=signed_old, idempotency_key="idem_1"
    )
    assert result.operation_state is OperationState.ADMISSIBILITY_DENIED
    assert result.reason == "stale_epoch"


def test_stale_epoch_release_rejected_by_receiver(world) -> None:
    """Even if the connector's check is bypassed, the receiver fence rejects it."""
    bo = world.create_operation()
    world.revocation.supersede(bo, source="test", receiver=world.receiver)
    resp = world.receiver.accept(
        business_operation_id=bo,
        release_id="rel_direct",
        release_epoch=0,
        idempotency_key="idem_direct",
        amount=1000,
        currency="USD",
    )
    assert resp.response_class is ReceiverResponseClass.REJECTED_FENCED
    assert world.oracle.refund_count() == 0