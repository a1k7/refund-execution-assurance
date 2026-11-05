"""TEST-ONLY oracle that reads the simulated receiver's internal ground truth.

This module is *not* importable from ``app/`` and is never used by the
verifier. It exists so tests can assert against the receiver's actual
behaviour (what the refund state really is) separately from what the receiver
*reported* and what the verifier *concluded*.

The security-negative test suite (``test_security_negative.py``) has a
regression test that greps ``app/`` for imports of this module.
"""
from __future__ import annotations

from typing import Dict, List

from app.receiver.simulated import (
    SimulatedRefundReceiver,
    _RefundRecord,
    _RefundState,
)


class ReceiverOracle:
    def __init__(self, receiver: SimulatedRefundReceiver) -> None:
        self._receiver = receiver

    def all_refunds(self) -> Dict[str, _RefundRecord]:
        return dict(self._receiver._refunds)

    def refund_states(self) -> List[str]:
        return [r.state.value for r in self._receiver._refunds.values()]

    def refund_state(self, receiver_refund_reference: str) -> _RefundState:
        return self._receiver._refunds[receiver_refund_reference].state

    def refund_record(self, receiver_refund_reference: str) -> _RefundRecord:
        return self._receiver._refunds[receiver_refund_reference]

    def refund_count(self) -> int:
        return len(self._receiver._refunds)

    def refunds_for_operation(self, business_operation_id: str) -> List[_RefundRecord]:
        return [
            r
            for r in self._receiver._refunds.values()
            if r.business_operation_id == business_operation_id
        ]

    def fencing_epoch(self, business_operation_id: str) -> int:
        return self._receiver._fencing._epochs.get(business_operation_id, 0)


__all__ = ["ReceiverOracle", "_RefundState", "_RefundRecord"]