"""Deterministic demo scenarios. Each runs against a fresh operation.

Every scenario is a pure function: it takes a ``ScenarioWorld`` and returns
a dict describing the operation + verifier report. Scenarios do NOT inspect
receiver ground truth — that is exclusively the test oracle's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..clock import Clock
from ..connector.dispatch_coordinator import DispatchCoordinator
from ..connector.hooks import DispatchContext, Hooks
from ..domain.enums import (
    EvidenceEventType,
    InFlightPolicy,
    OperationState,
    ReceiverResponseClass,
)
from ..domain.ids import new_business_operation_id, new_correlation_id, new_idempotency_key
from ..domain.models import SignedRelease
from ..evidence.schema import build_event
from ..receiver.simulated import SimulatedReceiverBehaviour


@dataclass
class ScenarioWorld:
    db: object
    clock: Clock
    receiver: object
    issuer: object
    release_verifier: object
    revocation: object
    ledger: object
    verifier: object
    operations: object


def _new_op(world: ScenarioWorld, policy: InFlightPolicy = InFlightPolicy.BLOCK_NEW_DISPATCHES) -> str:
    bo_id = new_business_operation_id()
    world.operations.create(bo_id, world.clock.now(), policy)
    world.ledger.append(build_event(
        event_type=EvidenceEventType.OPERATION_CREATED,
        evidence_source="demo",
        timestamp=world.clock.now(),
        business_operation_id=bo_id,
        new_state=OperationState.CREATED,
        payload={"in_flight_policy": policy.value},
        correlation_id=new_correlation_id(),
    ))
    return bo_id


def _issue(world: ScenarioWorld, bo_id: str, epoch: Optional[int] = None) -> SignedRelease:
    op = world.operations.get(bo_id)
    signed = world.issuer.issue(
        business_operation_id=bo_id,
        release_epoch=op.current_epoch if epoch is None else epoch,
        subject="agent-demo-1",
        audience="decisionassure:refunds",
        capability="refund.issue",
        policy_version="policy-2024-01",
        amount=1000,
        currency="USD",
        params_digest="sha256:demo",
    )
    from ..persistence.repositories import ReleaseRepository
    ReleaseRepository(world.db).insert(signed, world.clock.now())
    if op.state is OperationState.CREATED:
        from ..domain.state_machine import assert_transition
        assert_transition(op.state, OperationState.RELEASE_ISSUED, reason="demo-issue")
        world.operations.update_state(bo_id, OperationState.RELEASE_ISSUED, world.clock.now())
    world.ledger.append(build_event(
        event_type=EvidenceEventType.RELEASE_ISSUED,
        evidence_source="parmana-issuer",
        timestamp=world.clock.now(),
        business_operation_id=bo_id,
        release=signed.payload,
        previous_state=op.state,
        new_state=OperationState.RELEASE_ISSUED,
        correlation_id=new_correlation_id(),
    ))
    return signed


def _coordinator(world: ScenarioWorld, hooks: Optional[Hooks] = None) -> DispatchCoordinator:
    return DispatchCoordinator(
        db=world.db,
        clock=world.clock,
        release_verifier=world.release_verifier,
        revocation=world.revocation,
        receiver=world.receiver,
        hooks=hooks,
    )


def _reset_receiver_behaviour(world: ScenarioWorld) -> None:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour())


def _result(world: ScenarioWorld, bo_id: str, description: str, notes: List[str]) -> dict:
    op = world.operations.get(bo_id)
    report = world.verifier.verify(bo_id)
    return {
        "scenario_id": "",
        "business_operation_id": bo_id,
        "description": description,
        "operation": {
            "business_operation_id": op.business_operation_id,
            "state": op.state.value,
            "authority_state": op.authority_state.value,
            "in_flight_policy": op.in_flight_policy.value,
            "terminal_classification": op.terminal_classification.value if op.terminal_classification else None,
            "retry_eligible": op.retry_eligible,
            "reconciliation_required": op.reconciliation_required,
            "receiver_refund_reference": op.receiver_refund_reference,
            "current_epoch": op.current_epoch,
        },
        "verification_report": {
            "verification_status": report.verification_status.value,
            "business_operation_id": report.business_operation_id,
            "terminal_classification": report.terminal_classification.value if report.terminal_classification else None,
            "evidence_events_examined": report.evidence_events_examined,
            "failed_assertions": report.failed_assertions,
            "warnings": report.warnings,
            "unresolved_conditions": report.unresolved_conditions,
            "retry_eligible": report.retry_eligible,
            "reconciliation_required": report.reconciliation_required,
            "chain_valid": report.chain_valid,
        },
        "last_receiver_reported_status": None,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Individual scenarios
# ---------------------------------------------------------------------------


def scenario_01(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    bo = _new_op(world)
    signed = _issue(world, bo)
    world.revocation.revoke(bo, source="demo-revoke", receiver=world.receiver)
    dispatch = _coordinator(world).dispatch(
        business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
    )
    assert dispatch.operation_state is OperationState.ADMISSIBILITY_DENIED
    return _result(world, bo, "Revocation before dispatch: denied locally, receiver never called.",
                   ["dispatch was denied at the connector"])


def scenario_02(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    bo = _new_op(world)
    signed = _issue(world, bo)
    world.revocation.revoke(bo, source="demo-revoke", receiver=world.receiver)
    dispatch = _coordinator(world).dispatch(
        business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
    )
    assert dispatch.operation_state is OperationState.ADMISSIBILITY_DENIED
    return _result(world, bo, "Revocation before connector check: denied.", [])


def scenario_03(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    bo = _new_op(world)
    signed = _issue(world, bo)
    hooks = Hooks()

    def revoke_mid_dispatch(ctx: DispatchContext) -> None:
        world.revocation.revoke(bo, source="demo-mid-dispatch", receiver=world.receiver)

    hooks.on_before_receiver_call(revoke_mid_dispatch)
    dispatch = _coordinator(world, hooks).dispatch(
        business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
    )
    assert dispatch.response_class is ReceiverResponseClass.REJECTED_FENCED
    return _result(world, bo, "Revocation between check and receiver call: receiver fenced the stale epoch.",
                   ["receiver rejected with REJECTED_FENCED"])


def scenario_04(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    bo = _new_op(world, InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
    signed = _issue(world, bo)
    dispatch = _coordinator(world).dispatch(
        business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
    )
    assert dispatch.operation_state is OperationState.IN_FLIGHT
    world.revocation.revoke(bo, source="demo-revoke", receiver=world.receiver)
    from ..cancellation.service import CancellationService
    cancel = CancellationService(world.db, world.clock, world.receiver)
    new_state = cancel.request_cancellation(bo)
    assert new_state is OperationState.CANCELLATION_CONFIRMED, new_state
    return _result(world, bo, "Dispatch accepted, revocation, cancellation confirmed via status query.", [])


def scenario_05(world: ScenarioWorld) -> dict:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(complete_on_accept=True))
    try:
        bo = _new_op(world, InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = _issue(world, bo)
        dispatch = _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        assert dispatch.operation_state is OperationState.IN_FLIGHT
        world.revocation.revoke(bo, source="demo-revoke", receiver=world.receiver)
        from ..cancellation.service import CancellationService
        cancel = CancellationService(world.db, world.clock, world.receiver)
        new_state = cancel.request_cancellation(bo)
        assert new_state is OperationState.COMPLETION_CONFIRMED, new_state
        return _result(world, bo, "Receiver completed before cancellation: COMPLETION_CONFIRMED.", [])
    finally:
        _reset_receiver_behaviour(world)


def scenario_06(world: ScenarioWorld) -> dict:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        cancel_transitions_to_terminal=True,
        status_class_override=ReceiverResponseClass.STATUS_UNKNOWN,
    ))
    try:
        bo = _new_op(world, InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = _issue(world, bo)
        _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        world.revocation.revoke(bo, source="demo-revoke", receiver=world.receiver)
        from ..cancellation.service import CancellationService
        cancel = CancellationService(world.db, world.clock, world.receiver)
        new_state = cancel.request_cancellation(bo)
        assert new_state is OperationState.OUTCOME_UNKNOWN, new_state
        return _result(world, bo, "Cancellation ack but status inconclusive: OUTCOME_UNKNOWN, retry blocked.", [])
    finally:
        _reset_receiver_behaviour(world)


def scenario_07(world: ScenarioWorld) -> dict:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        complete_on_accept=True,
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = _new_op(world)
        signed = _issue(world, bo)
        dispatch = _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        assert dispatch.operation_state is OperationState.OUTCOME_UNKNOWN
        return _result(world, bo, "Receiver completed internally but reported INCONCLUSIVE: verifier says OUTCOME_UNKNOWN.",
                       ["receiver ground truth is COMPLETED but the verifier has no evidence of that"])
    finally:
        _reset_receiver_behaviour(world)


def scenario_08(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    bo = _new_op(world, InFlightPolicy.BLOCK_NEW_DISPATCHES)
    signed = _issue(world, bo)
    _coordinator(world).dispatch(
        business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
    )
    world.revocation.revoke(bo, source="demo-revoke", receiver=world.receiver)
    op = world.operations.get(bo)
    assert op.state is OperationState.IN_FLIGHT
    return _result(world, bo, "Default BLOCK_NEW_DISPATCHES: no cancel request sent, operation stays IN_FLIGHT.",
                   ["no cancellation attempted"])


def scenario_09(world: ScenarioWorld) -> dict:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = _new_op(world)
        signed = _issue(world, bo)
        _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        op = world.operations.get(bo)
        assert op.state is OperationState.OUTCOME_UNKNOWN
        second = _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        assert "duplicate_guard" in (second.reason or ""), second.reason
        return _result(world, bo, "Unknown outcome blocks a retry with a new idempotency key.",
                       ["retry blocked pending reconciliation"])
    finally:
        _reset_receiver_behaviour(world)


def scenario_10(world: ScenarioWorld) -> dict:
    """Sequential view of concurrency. The authoritative concurrent check lives in
    tests/test_concurrency.py (threading.Barrier). See scenario notes."""
    _reset_receiver_behaviour(world)
    bo = _new_op(world)
    signed = _issue(world, bo)
    first = _coordinator(world).dispatch(
        business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
    )
    second = _coordinator(world).dispatch(
        business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
    )
    assert "duplicate_guard" in (second.reason or "") or "release_already_consumed" in (second.reason or "")
    return _result(world, bo, "Sequential dispatch of the same release: second attempt blocked.",
                   ["see tests/test_concurrency.py for the threaded version"])


def scenario_11(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        accept_class_override=ReceiverResponseClass.REJECTED_INVALID,
    ))
    try:
        bo = _new_op(world)
        signed = _issue(world, bo)
        _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        # Same release, new idempotency key: duplicate guard allows (DISPATCH_REJECTED),
        # then atomic consumption rejects because the release is already consumed.
        replay = _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        assert replay.reason == "release_already_consumed", replay.reason
        return _result(world, bo, "Replay of a consumed release rejected by atomic consumption.",
                       ["no second receiver call was made"])
    finally:
        _reset_receiver_behaviour(world)


def scenario_12(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    bo = _new_op(world)
    signed = _issue(world, bo)

    world.clock.advance(400)
    r1 = _coordinator(world).dispatch(business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key())
    assert "expired" in (r1.reason or "")

    bad = signed.model_copy(deep=True)
    bad.payload.amount = 999_999
    r2 = _coordinator(world).dispatch(business_operation_id=bo, release=bad, idempotency_key=new_idempotency_key())
    assert "invalid_signature" in (r2.reason or "")

    bad_sig = signed.model_copy(deep=True)
    bad_sig.signature.signature_b64 = "AAAA"
    r3 = _coordinator(world).dispatch(business_operation_id=bo, release=bad_sig, idempotency_key=new_idempotency_key())
    assert "invalid_signature" in (r3.reason or "")

    wrong_aud = _issue(world, bo)
    wrong_aud.payload.audience = "other-audience"
    r4 = _coordinator(world).dispatch(business_operation_id=bo, release=wrong_aud, idempotency_key=new_idempotency_key())
    assert "invalid_signature" in (r4.reason or "")

    wrong_cap = _issue(world, bo)
    wrong_cap.payload.capability = "refund.approve"
    r5 = _coordinator(world).dispatch(business_operation_id=bo, release=wrong_cap, idempotency_key=new_idempotency_key())
    assert "invalid_signature" in (r5.reason or "")

    return _result(world, bo, "Five invalid releases rejected before dispatch.",
                   ["expired", "tampered", "bad sig", "wrong audience", "wrong capability"])


def scenario_13(world: ScenarioWorld) -> dict:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        supports_cancellation=False,
        supports_status_query=False,
    ))
    try:
        bo = _new_op(world, InFlightPolicy.REQUEST_CANCELLATION_OF_IN_FLIGHT)
        signed = _issue(world, bo)
        _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        world.revocation.revoke(bo, source="demo-revoke", receiver=world.receiver)
        from ..cancellation.service import CancellationService
        from ..domain.errors import ReceiverCapabilityError
        cancel = CancellationService(world.db, world.clock, world.receiver)
        try:
            cancel.request_cancellation(bo)
            raised = False
        except ReceiverCapabilityError:
            raised = True
        assert raised, "expected ReceiverCapabilityError"
        return _result(world, bo, "Receiver without cancellation capability: config rejected, no cancel attempted.",
                       ["fail-closed by capability check"])
    finally:
        _reset_receiver_behaviour(world)


def scenario_14(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    bo = _new_op(world)
    _issue(world, bo)
    ok_before = world.ledger.verify_chain()
    # SQLite compiled without SQLITE_ENABLE_UPDATE_DELETE_LIMIT rejects
    # ``UPDATE ... LIMIT``. Select the sequence explicitly, then update by it.
    with world.db.transaction() as c:
        row = c.execute(
            "SELECT MIN(sequence) AS s FROM evidence WHERE business_operation_id = ?",
            (bo,),
        ).fetchone()
        c.execute(
            "UPDATE evidence SET event_json = '{\"tampered\":true}' WHERE sequence = ?",
            (row["s"],),
        )
    ok_after = world.ledger.verify_chain()
    assert ok_before is True
    assert ok_after is False
    report = world.verifier.verify(bo)
    assert "chain_invalid" in report.failed_assertions
    return _result(world, bo, "Evidence tampering detected by hash chain.",
                   ["hash chain verification returned False after tampering"])


def scenario_15(world: ScenarioWorld) -> dict:
    world.receiver.set_behaviour(SimulatedReceiverBehaviour(
        complete_on_accept=True,
        accept_class_override=ReceiverResponseClass.INCONCLUSIVE,
    ))
    try:
        bo = _new_op(world)
        signed = _issue(world, bo)
        _coordinator(world).dispatch(
            business_operation_id=bo, release=signed, idempotency_key=new_idempotency_key()
        )
        op = world.operations.get(bo)
        assert op.state is OperationState.OUTCOME_UNKNOWN
        world.receiver.set_behaviour(SimulatedReceiverBehaviour(complete_on_accept=True))
        from ..reconciliation.service import ReconciliationService
        recon = ReconciliationService(world.db, world.clock, world.receiver)
        new_state = recon.reconcile(bo)
        assert new_state is OperationState.COMPLETION_CONFIRMED, new_state
        return _result(world, bo, "Reconciliation resolves OUTCOME_UNKNOWN to COMPLETION_CONFIRMED.", [])
    finally:
        _reset_receiver_behaviour(world)


def scenario_16(world: ScenarioWorld) -> dict:
    _reset_receiver_behaviour(world)
    bo = _new_op(world)
    signed_old = _issue(world, bo, epoch=0)
    world.revocation.supersede(bo, source="demo-supersede", receiver=world.receiver)
    dispatch = _coordinator(world).dispatch(
        business_operation_id=bo, release=signed_old, idempotency_key=new_idempotency_key()
    )
    assert dispatch.operation_state is OperationState.ADMISSIBILITY_DENIED
    assert dispatch.reason == "stale_epoch", dispatch.reason
    receiver_resp = world.receiver.accept(
        business_operation_id=bo,
        release_id=signed_old.payload.release_id,
        release_epoch=signed_old.payload.release_epoch,
        idempotency_key=new_idempotency_key(),
        amount=signed_old.payload.amount,
        currency=signed_old.payload.currency,
    )
    assert receiver_resp.response_class is ReceiverResponseClass.REJECTED_FENCED
    return _result(world, bo, "Stale epoch: connector denies locally; receiver also rejects on fencing.",
                   ["defense in depth confirmed"])


SCENARIOS: Dict[str, Callable[[ScenarioWorld], dict]] = {
    "01": scenario_01, "02": scenario_02, "03": scenario_03, "04": scenario_04,
    "05": scenario_05, "06": scenario_06, "07": scenario_07, "08": scenario_08,
    "09": scenario_09, "10": scenario_10, "11": scenario_11, "12": scenario_12,
    "13": scenario_13, "14": scenario_14, "15": scenario_15, "16": scenario_16,
}