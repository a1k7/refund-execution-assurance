"""Repositories. Each method is a single SQL operation or wraps a single transaction.

The atomic release-consumption method is the only place in the codebase where a
business-critical compare-and-set happens; see ``ReleaseRepository.try_consume``.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..domain.enums import (
    AuthorityState,
    InFlightPolicy,
    OperationState,
    ReceiverResponseClass,
    TerminalClassification,
)
from ..domain.models import (
    DispatchAttemptRecord,
    OperationRecord,
    ReleasePayload,
    ReleaseSignature,
    RevocationRecord,
    SignedRelease,
)
from .db import Database


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime) -> str:
    return value.isoformat()


class OperationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, business_operation_id: str, now: datetime, policy: InFlightPolicy) -> OperationRecord:
        with self.db.transaction() as c:
            c.execute(
                """
                INSERT INTO operations (
                    business_operation_id, state, authority_state, in_flight_policy,
                    retry_eligible, reconciliation_required, current_epoch, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?)
                """,
                (
                    business_operation_id,
                    OperationState.CREATED.value,
                    AuthorityState.ACTIVE.value,
                    policy.value,
                    _iso(now),
                    _iso(now),
                ),
            )
        rec = self.get(business_operation_id)
        assert rec is not None
        return rec

    def get(self, business_operation_id: str) -> Optional[OperationRecord]:
        row = self.db.query_one(
            "SELECT * FROM operations WHERE business_operation_id = ?", (business_operation_id,)
        )
        return self._to_model(row) if row else None

    def list_all(self) -> List[OperationRecord]:
        rows = self.db.query("SELECT * FROM operations ORDER BY created_at ASC")
        return [self._to_model(r) for r in rows]

    def update_state(self, business_operation_id: str, new_state: OperationState, now: datetime) -> None:
        with self.db.transaction() as c:
            c.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE business_operation_id = ?",
                (new_state.value, _iso(now), business_operation_id),
            )

    def update_fields(self, business_operation_id: str, now: datetime, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "state",
            "authority_state",
            "in_flight_policy",
            "terminal_classification",
            "retry_eligible",
            "reconciliation_required",
            "receiver_refund_reference",
            "current_epoch",
        }
        sets: List[str] = []
        params: List[Any] = []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"Unknown operation field: {k}")
            if hasattr(v, "value"):
                v = v.value
            if isinstance(v, bool):
                v = 1 if v else 0
            sets.append(f"{k} = ?")
            params.append(v)
        sets.append("updated_at = ?")
        params.append(_iso(now))
        params.append(business_operation_id)
        with self.db.transaction() as c:
            c.execute(f"UPDATE operations SET {', '.join(sets)} WHERE business_operation_id = ?", tuple(params))

    def bump_epoch(self, business_operation_id: str, now: datetime) -> int:
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT current_epoch FROM operations WHERE business_operation_id = ?",
                (business_operation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(business_operation_id)
            new_epoch = int(row["current_epoch"]) + 1
            c.execute(
                "UPDATE operations SET current_epoch = ?, updated_at = ? WHERE business_operation_id = ?",
                (new_epoch, _iso(now), business_operation_id),
            )
        return new_epoch

    def _to_model(self, row: Any) -> OperationRecord:
        return OperationRecord(
            business_operation_id=row["business_operation_id"],
            state=OperationState(row["state"]),
            authority_state=AuthorityState(row["authority_state"]),
            in_flight_policy=InFlightPolicy(row["in_flight_policy"]),
            terminal_classification=(
                TerminalClassification(row["terminal_classification"])
                if row["terminal_classification"]
                else None
            ),
            retry_eligible=bool(row["retry_eligible"]),
            reconciliation_required=bool(row["reconciliation_required"]),
            receiver_refund_reference=row["receiver_refund_reference"],
            current_epoch=int(row["current_epoch"]),
            created_at=_dt(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_dt(row["updated_at"]),  # type: ignore[arg-type]
        )


class ReleaseRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert(self, signed: SignedRelease, now: datetime) -> None:
        payload = signed.payload
        with self.db.transaction() as c:
            c.execute(
                """
                INSERT INTO releases (
                    release_id, business_operation_id, release_epoch,
                    payload_json, signature_json, issued_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.release_id,
                    payload.business_operation_id,
                    payload.release_epoch,
                    json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
                    json.dumps(signed.signature.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
                    _iso(payload.issued_at),
                    _iso(payload.expires_at),
                ),
            )

    def get(self, release_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM releases WHERE release_id = ?", (release_id,))
        if row is None:
            return None
        return {
            "release_id": row["release_id"],
            "business_operation_id": row["business_operation_id"],
            "release_epoch": int(row["release_epoch"]),
            "payload_json": row["payload_json"],
            "signature_json": row["signature_json"],
            "issued_at": row["issued_at"],
            "expires_at": row["expires_at"],
            "consumed_at": row["consumed_at"],
            "consumed_by_attempt_id": row["consumed_by_attempt_id"],
            "verified_at": row["verified_at"],
            "verification_result": row["verification_result"],
        }

    def mark_verified(self, release_id: str, verified: bool, result: str, now: datetime) -> None:
        with self.db.transaction() as c:
            c.execute(
                "UPDATE releases SET verified_at = ?, verification_result = ? WHERE release_id = ?",
                (_iso(now), result if verified else f"FAILED:{result}", release_id),
            )

    def try_consume(self, release_id: str, dispatch_attempt_id: str, now: datetime) -> bool:
        """Atomic compare-and-set.

        Returns True exactly once per unconsumed release_id. Any concurrent caller
        that observes ``consumed_at IS NOT NULL`` (or that loses the row-level race)
        gets False.

        This is a *local* atomic reservation only. It does not prove the external
        receiver accepted the request — that requires receiver-side fencing.
        """
        with self.db.transaction() as c:
            cur = c.execute(
                "UPDATE releases SET consumed_at = ?, consumed_by_attempt_id = ? "
                "WHERE release_id = ? AND consumed_at IS NULL",
                (_iso(now), dispatch_attempt_id, release_id),
            )
            return cur.rowcount == 1


class DispatchAttemptRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        dispatch_attempt_id: str,
        business_operation_id: str,
        release_id: str,
        release_epoch: int,
        idempotency_key: str,
        state: OperationState,
        now: datetime,
    ) -> None:
        with self.db.transaction() as c:
            c.execute(
                """
                INSERT INTO dispatch_attempts (
                    dispatch_attempt_id, business_operation_id, release_id, release_epoch,
                    idempotency_key, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dispatch_attempt_id,
                    business_operation_id,
                    release_id,
                    release_epoch,
                    idempotency_key,
                    state.value,
                    _iso(now),
                    _iso(now),
                ),
            )

    def update(
        self,
        dispatch_attempt_id: str,
        now: datetime,
        *,
        state: Optional[OperationState] = None,
        response_class: Optional[ReceiverResponseClass] = None,
        receiver_refund_reference: Optional[str] = None,
    ) -> None:
        sets: List[str] = []
        params: List[Any] = []
        if state is not None:
            sets.append("state = ?")
            params.append(state.value)
        if response_class is not None:
            sets.append("response_class = ?")
            params.append(response_class.value)
        if receiver_refund_reference is not None:
            sets.append("receiver_refund_reference = ?")
            params.append(receiver_refund_reference)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_iso(now))
        params.append(dispatch_attempt_id)
        with self.db.transaction() as c:
            c.execute(f"UPDATE dispatch_attempts SET {', '.join(sets)} WHERE dispatch_attempt_id = ?", tuple(params))

    def get(self, dispatch_attempt_id: str) -> Optional[DispatchAttemptRecord]:
        row = self.db.query_one(
            "SELECT * FROM dispatch_attempts WHERE dispatch_attempt_id = ?", (dispatch_attempt_id,)
        )
        return self._to_model(row) if row else None

    def list_for_operation(self, business_operation_id: str) -> List[DispatchAttemptRecord]:
        rows = self.db.query(
            "SELECT * FROM dispatch_attempts WHERE business_operation_id = ? ORDER BY created_at ASC",
            (business_operation_id,),
        )
        return [self._to_model(r) for r in rows]

    def _to_model(self, row: Any) -> DispatchAttemptRecord:
        return DispatchAttemptRecord(
            dispatch_attempt_id=row["dispatch_attempt_id"],
            business_operation_id=row["business_operation_id"],
            release_id=row["release_id"],
            release_epoch=int(row["release_epoch"]),
            idempotency_key=row["idempotency_key"],
            state=OperationState(row["state"]),
            response_class=ReceiverResponseClass(row["response_class"]) if row["response_class"] else None,
            receiver_refund_reference=row["receiver_refund_reference"],
            created_at=_dt(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_dt(row["updated_at"]),  # type: ignore[arg-type]
        )


class RevocationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert(self, business_operation_id: str, source: str, new_epoch: int, now: datetime) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO revocation_events (business_operation_id, revoked_at, source, new_epoch) "
                "VALUES (?, ?, ?, ?)",
                (business_operation_id, _iso(now), source, new_epoch),
            )

    def is_revoked(self, business_operation_id: str) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM revocation_events WHERE business_operation_id = ? LIMIT 1",
            (business_operation_id,),
        )
        return row is not None

    def list_for_operation(self, business_operation_id: str) -> List[RevocationRecord]:
        rows = self.db.query(
            "SELECT * FROM revocation_events WHERE business_operation_id = ? ORDER BY id ASC",
            (business_operation_id,),
        )
        return [
            RevocationRecord(
                id=int(r["id"]),
                business_operation_id=r["business_operation_id"],
                revoked_at=_dt(r["revoked_at"]),  # type: ignore[arg-type]
                source=r["source"],
                new_epoch=int(r["new_epoch"]),
            )
            for r in rows
        ]


class FencingRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, business_operation_id: str, current_epoch: int, now: datetime) -> None:
        with self.db.transaction() as c:
            c.execute(
                """
                INSERT INTO fencing_state (business_operation_id, current_epoch, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(business_operation_id) DO UPDATE SET
                    current_epoch = excluded.current_epoch,
                    updated_at = excluded.updated_at
                """,
                (business_operation_id, current_epoch, _iso(now)),
            )

    def get(self, business_operation_id: str) -> Optional[int]:
        row = self.db.query_one(
            "SELECT current_epoch FROM fencing_state WHERE business_operation_id = ?",
            (business_operation_id,),
        )
        return int(row["current_epoch"]) if row else None


class EvidenceRepository:
    """Low-level row access. Business code uses ``EvidenceLedger`` (app/evidence/ledger.py)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def insert(
        self,
        event_id: str,
        event_type: str,
        business_operation_id: Optional[str],
        event_json: str,
        prev_hash: str,
        event_hash: str,
        now: datetime,
    ) -> int:
        with self.db.transaction() as c:
            cur = c.execute(
                """
                INSERT INTO evidence (event_id, event_type, business_operation_id, event_json, prev_hash, hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, event_type, business_operation_id, event_json, prev_hash, event_hash, _iso(now)),
            )
            return int(cur.lastrowid)

    def last(self) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM evidence ORDER BY sequence DESC LIMIT 1")
        return dict(row) if row else None

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM evidence ORDER BY sequence ASC")
        return [dict(r) for r in rows]

    def list_for_operation(self, business_operation_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM evidence WHERE business_operation_id = ? ORDER BY sequence ASC",
            (business_operation_id,),
        )
        return [dict(r) for r in rows]

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM evidence")
        assert row is not None
        return int(row["n"])