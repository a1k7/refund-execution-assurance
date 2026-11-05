"""Authority service (organization's revocation source of truth).

Responsibilities
----------------
1. Record a revocation event.
2. Bump the operation's current epoch (fencing generation).
3. Flip ``operations.authority_state`` to REVOKED.
4. Mirror the new epoch into ``fencing_state`` (observation only).
5. If a receiver implementing ``FenceUpdatable`` is passed in, propagate the
   new epoch to the receiver's authoritative fencing state.

``supersede()`` bumps the epoch WITHOUT changing authority state — used when a
new release epoch must be issued for policy/approval reasons, making prior
releases stale without revoking authority.

Trust assumptions
-----------------
Receiver-side fencing works ONLY IF the receiver's fencing state is
authoritative and sufficiently fresh at the moment it accepts a dispatch.
In this reference implementation propagation is synchronous. In production
it will be asynchronous with an explicit freshness bound. See
THREAT_MODEL.md §"Receiver fencing freshness" and INTEGRATION_CONTRACT.md.
"""
from __future__ import annotations

from typing import Optional, Protocol

from ..clock import Clock
from ..domain.enums import AuthorityState, EvidenceEventType
from ..domain.ids import new_correlation_id
from ..evidence.ledger import EvidenceLedger
from ..evidence.schema import build_event
from ..persistence.db import Database
from ..persistence.repositories import (
    FencingRepository,
    OperationRepository,
    RevocationRepository,
)


class FenceUpdatable(Protocol):
    """Minimal receiver interface required to enforce epoch fencing."""

    def update_fencing_state(self, business_operation_id: str, current_epoch: int) -> None: ...


class RevocationService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock
        self.ops = OperationRepository(db)
        self.revs = RevocationRepository(db)
        self.fence = FencingRepository(db)
        self.ledger = EvidenceLedger(db)

    def revoke(
        self,
        business_operation_id: str,
        *,
        source: str,
        receiver: Optional[FenceUpdatable] = None,
    ) -> int:
        op = self.ops.get(business_operation_id)
        if op is None:
            raise KeyError(business_operation_id)

        now = self.clock.now()
        new_epoch = self.ops.bump_epoch(business_operation_id, now)
        self.ops.update_fields(
            business_operation_id,
            now,
            authority_state=AuthorityState.REVOKED,
        )
        self.revs.insert(
            business_operation_id,
            source=source,
            new_epoch=new_epoch,
            now=now,
        )
        self.fence.upsert(business_operation_id, new_epoch, now)
        if receiver is not None:
            receiver.update_fencing_state(business_operation_id, new_epoch)

        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.AUTHORITY_REVOKED,
                evidence_source="authority-service",
                timestamp=now,
                business_operation_id=business_operation_id,
                authority_state=AuthorityState.REVOKED,
                payload={"new_epoch": new_epoch, "source": source, "kind": "revoke"},
                correlation_id=new_correlation_id(),
            )
        )
        return new_epoch

    def supersede(
        self,
        business_operation_id: str,
        *,
        source: str,
        receiver: Optional[FenceUpdatable] = None,
    ) -> int:
        """Bump epoch without changing authority state. Prior releases become stale."""
        op = self.ops.get(business_operation_id)
        if op is None:
            raise KeyError(business_operation_id)

        now = self.clock.now()
        new_epoch = self.ops.bump_epoch(business_operation_id, now)
        self.fence.upsert(business_operation_id, new_epoch, now)
        if receiver is not None:
            receiver.update_fencing_state(business_operation_id, new_epoch)

        self.ledger.append(
            build_event(
                event_type=EvidenceEventType.AUTHORITY_REVOKED,
                evidence_source="authority-service",
                timestamp=now,
                business_operation_id=business_operation_id,
                authority_state=op.authority_state,
                payload={"new_epoch": new_epoch, "source": source, "kind": "supersede"},
                correlation_id=new_correlation_id(),
            )
        )
        return new_epoch

    def is_revoked(self, business_operation_id: str) -> bool:
        return self.revs.is_revoked(business_operation_id)