"""Receiver protocol.

The receiver is the sole authority on refund acceptance, cancellation
acknowledgment, and status. The connector can only call methods defined here.
The verifier CANNOT call these methods — it only reads recorded evidence.

Capabilities are declared by the receiver and are enforced by the cancellation
service (a receiver that does not support cancellation cannot have the
REQUEST_CANCELLATION_OF_IN_FLIGHT policy applied).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.models import ReceiverResponse


@dataclass(frozen=True)
class ReceiverCapabilities:
    supports_cancellation: bool
    supports_status_query: bool


class ReceiverProtocol(Protocol):
    @property
    def capabilities(self) -> ReceiverCapabilities: ...

    def accept(
        self,
        *,
        business_operation_id: str,
        release_id: str,
        release_epoch: int,
        idempotency_key: str,
        amount: int,
        currency: str,
    ) -> ReceiverResponse: ...

    def cancel(
        self,
        *,
        business_operation_id: str,
        receiver_refund_reference: str,
    ) -> ReceiverResponse: ...

    def query_status(
        self,
        *,
        business_operation_id: str,
        receiver_refund_reference: str,
    ) -> ReceiverResponse: ...

    def update_fencing_state(
        self,
        business_operation_id: str,
        current_epoch: int,
    ) -> None: ...