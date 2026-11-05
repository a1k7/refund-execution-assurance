"""Evidence ledger: hash chain, tamper detection, per-operation reads."""
from __future__ import annotations

import json

from app.domain.enums import EvidenceEventType, OperationState
from app.evidence.schema import build_event


def _make_event(world, bo: str, event_type: EvidenceEventType = EvidenceEventType.OPERATION_CREATED):
    return build_event(
        event_type=event_type,
        evidence_source="test",
        timestamp=world.clock.now(),
        business_operation_id=bo,
        previous_state=None,
        new_state=OperationState.CREATED,
    )


def test_append_and_verify_chain(world) -> None:
    bo = world.create_operation()
    for _ in range(3):
        world.ledger.append(_make_event(world, bo))
    assert world.ledger.verify_chain() is True


def test_empty_ledger_is_valid(world) -> None:
    assert world.ledger.verify_chain() is True


def test_first_event_uses_genesis_prev_hash(world) -> None:
    bo = world.create_operation()
    world.ledger.append(_make_event(world, bo))
    rows = world.ledger.read_all()
    assert rows[0]["_prev_hash"] == ""


def test_second_event_prev_hash_matches_first_event_hash(world) -> None:
    bo = world.create_operation()
    world.ledger.append(_make_event(world, bo))
    world.ledger.append(_make_event(world, bo))
    rows = world.ledger.read_all()
    assert rows[1]["_prev_hash"] == rows[0]["_hash"]


def test_tampering_with_event_payload_breaks_chain(world) -> None:
    bo = world.create_operation()
    world.ledger.append(_make_event(world, bo))
    world.ledger.append(_make_event(world, bo))
    assert world.ledger.verify_chain() is True

    with world.db.transaction() as c:
        first_seq = c.execute("SELECT MIN(sequence) AS s FROM evidence").fetchone()["s"]
        c.execute(
            "UPDATE evidence SET event_json = ? WHERE sequence = ?",
            (json.dumps({"tampered": True}, sort_keys=True, separators=(",", ":")), first_seq),
        )

    assert world.ledger.verify_chain() is False


def test_deleting_middle_event_breaks_chain(world) -> None:
    bo = world.create_operation()
    world.ledger.append(_make_event(world, bo))  # seq 1
    world.ledger.append(_make_event(world, bo))  # seq 2
    world.ledger.append(_make_event(world, bo))  # seq 3
    assert world.ledger.verify_chain() is True

    with world.db.transaction() as c:
        c.execute("DELETE FROM evidence WHERE sequence = 2")
    # Now prev_hash of seq 3 no longer matches the recomputed prev (seq 1's hash).
    assert world.ledger.verify_chain() is False


def test_read_for_operation_returns_only_that_operation(world) -> None:
    bo_a = world.create_operation()   # 1 event: OPERATION_CREATED
    bo_b = world.create_operation()   # 1 event: OPERATION_CREATED
    world.ledger.append(_make_event(world, bo_a))  # bo_a: 2
    world.ledger.append(_make_event(world, bo_a))  # bo_a: 3
    world.ledger.append(_make_event(world, bo_b))  # bo_b: 2

    rows_a = world.ledger.read_for_operation(bo_a)
    rows_b = world.ledger.read_for_operation(bo_b)
    assert len(rows_a) == 3
    assert len(rows_b) == 2
    assert all(r["business_operation_id"] == bo_a for r in rows_a)
    assert all(r["business_operation_id"] == bo_b for r in rows_b)


def test_global_verify_works_even_with_multiple_operations(world) -> None:
    bo_a = world.create_operation()
    bo_b = world.create_operation()
    world.ledger.append(_make_event(world, bo_a))
    world.ledger.append(_make_event(world, bo_b))
    world.ledger.append(_make_event(world, bo_a))
    assert world.ledger.verify_chain() is True