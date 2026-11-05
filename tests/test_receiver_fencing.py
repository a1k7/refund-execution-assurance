"""Receiver-side epoch fencing."""
from __future__ import annotations

from app.domain.enums import ReceiverResponseClass


def _accept(world, bo: str, epoch: int) -> ReceiverResponseClass:
    resp = world.receiver.accept(
        business_operation_id=bo,
        release_id=f"rel_test_{epoch}",
        release_epoch=epoch,
        idempotency_key=f"idem_{epoch}",
        amount=1000,
        currency="USD",
    )
    return resp.response_class


def test_accept_with_no_fence_installed_is_accepted(world) -> None:
    bo = world.create_operation()
    assert _accept(world, bo, 0) is ReceiverResponseClass.ACCEPTED


def test_stale_epoch_is_rejected_after_fence_update(world) -> None:
    bo = world.create_operation()
    world.receiver.update_fencing_state(bo, 5)
    assert _accept(world, bo, 4) is ReceiverResponseClass.REJECTED_FENCED


def test_current_epoch_is_accepted(world) -> None:
    bo = world.create_operation()
    world.receiver.update_fencing_state(bo, 3)
    assert _accept(world, bo, 3) is ReceiverResponseClass.ACCEPTED


def test_future_epoch_is_accepted_and_receiver_does_not_auto_advance(world) -> None:
    bo = world.create_operation()
    world.receiver.update_fencing_state(bo, 2)
    # Presenting epoch 10 passes; but the receiver's fence does not move to 10
    # automatically. That's the authority service's job.
    assert _accept(world, bo, 10) is ReceiverResponseClass.ACCEPTED
    assert world.oracle.fencing_epoch(bo) == 2


def test_fence_update_is_monotonic(world) -> None:
    """Lower epoch updates are ignored; only strictly increasing updates move the fence."""
    bo = world.create_operation()
    world.receiver.update_fencing_state(bo, 5)
    world.receiver.update_fencing_state(bo, 3)  # ignored
    assert world.oracle.fencing_epoch(bo) == 5


def test_revocation_propagates_epoch_to_receiver(world) -> None:
    bo = world.create_operation()
    # pre-revocation epoch is 0; revoke bumps to 1
    new_epoch = world.revocation.revoke(bo, source="test", receiver=world.receiver)
    assert new_epoch == 1
    assert world.oracle.fencing_epoch(bo) == 1


def test_supersede_propagates_epoch_to_receiver_without_revoking(world) -> None:
    bo = world.create_operation()
    new_epoch = world.revocation.supersede(bo, source="test", receiver=world.receiver)
    assert new_epoch == 1
    assert world.oracle.fencing_epoch(bo) == 1
    op = world.operations.get(bo)
    assert op is not None
    assert op.authority_state.value == "ACTIVE"