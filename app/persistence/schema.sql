-- Core operation record. One row per business refund.
CREATE TABLE IF NOT EXISTS operations (
    business_operation_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    authority_state TEXT NOT NULL,
    in_flight_policy TEXT NOT NULL,
    terminal_classification TEXT,
    retry_eligible INTEGER NOT NULL DEFAULT 0,
    reconciliation_required INTEGER NOT NULL DEFAULT 0,
    receiver_refund_reference TEXT,
    current_epoch INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Issued releases (a single business operation may have multiple over time).
CREATE TABLE IF NOT EXISTS releases (
    release_id TEXT PRIMARY KEY,
    business_operation_id TEXT NOT NULL,
    release_epoch INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    signature_json TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    consumed_by_attempt_id TEXT,
    verified_at TEXT,
    verification_result TEXT,
    FOREIGN KEY (business_operation_id) REFERENCES operations (business_operation_id)
);

-- Dispatch attempts (many per operation across retries).
CREATE TABLE IF NOT EXISTS dispatch_attempts (
    dispatch_attempt_id TEXT PRIMARY KEY,
    business_operation_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    release_epoch INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    response_class TEXT,
    receiver_refund_reference TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (business_operation_id) REFERENCES operations (business_operation_id),
    FOREIGN KEY (release_id) REFERENCES releases (release_id)
);

-- Append-only hash-chained evidence ledger.
CREATE TABLE IF NOT EXISTS evidence (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    business_operation_id TEXT,
    event_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Revocation source of truth (mirrors what the simulated Parmana authority service knows).
CREATE TABLE IF NOT EXISTS revocation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_operation_id TEXT NOT NULL,
    revoked_at TEXT NOT NULL,
    source TEXT NOT NULL,
    new_epoch INTEGER NOT NULL
);

-- Receiver fencing state. Stands in for the receiver's authoritative epoch store.
-- In the real world this would live at the receiver, not in DecisionAssure.
CREATE TABLE IF NOT EXISTS fencing_state (
    business_operation_id TEXT PRIMARY KEY,
    current_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_op ON evidence (business_operation_id, sequence);
CREATE INDEX IF NOT EXISTS idx_releases_op ON releases (business_operation_id);
CREATE INDEX IF NOT EXISTS idx_attempts_op ON dispatch_attempts (business_operation_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_idem_unique ON dispatch_attempts (idempotency_key);