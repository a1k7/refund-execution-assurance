"""Deterministic hook points used by tests to inject events between connector steps.

The connector fires these synchronously; no sleeps, no timers, no threads.
Scenario 3 (revocation between admissibility check and receiver acceptance)
subscribes to ``before_receiver_call`` and revokes authority at that exact point.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Tuple

from ..domain.models import ReceiverResponse


@dataclass(frozen=True)
class DispatchContext:
    business_operation_id: str
    release_id: str
    release_epoch: int
    dispatch_attempt_id: str
    idempotency_key: str


BeforeReceiverHook = Callable[[DispatchContext], None]
AfterReceiverHook = Callable[[DispatchContext, ReceiverResponse], None]


class Hooks:
    def __init__(self) -> None:
        self._before: List[BeforeReceiverHook] = []
        self._after: List[AfterReceiverHook] = []

    def on_before_receiver_call(self, cb: BeforeReceiverHook) -> None:
        self._before.append(cb)

    def on_after_receiver_response(self, cb: AfterReceiverHook) -> None:
        self._after.append(cb)

    def fire_before_receiver_call(self, ctx: DispatchContext) -> None:
        for cb in list(self._before):
            cb(ctx)

    def fire_after_receiver_response(self, ctx: DispatchContext, resp: ReceiverResponse) -> None:
        for cb in list(self._after):
            cb(ctx, resp)

    def clear(self) -> None:
        self._before.clear()
        self._after.clear()

    def snapshot(self) -> Tuple[int, int]:
        return (len(self._before), len(self._after))