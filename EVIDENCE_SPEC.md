```markdown
# Evidence specification

## 1. Purpose

The evidence ledger is an append-only, hash-chained sequence of structured
events. It is the **only** input the independent verifier consumes.

The ledger is intentionally simple so that the invariant it enforces — and
what it does *not* enforce — can be stated precisely.

## 2. Event schema (schema version 1.0)

Every event is a `EvidenceEvent` (see `app/domain/models.py`). Fields:

| Field                      | Type                          | Notes |
|----------------------------|-------------------------------|-------|
| `event_id`                 | string (`evt_<hex>`)          | unique |
| `event_type`               | enum `EvidenceEventType`      | see §3 |
| `schema_version`           | string                        | currently "1.0" |
| `business_operation_id`    | string or null                | required for operational events |
| `release_id`               | string or null                | |
| `release_epoch`            | int or null                   | |
| `dispatch_attempt_id`      | string or null                | |
| `idempotency_key`          | string or null                | |
| `receiver_refund_reference`| string or null                | populated once known |
| `subject`                  | string or null                | agent identity |
| `audience`                 | string or null                | |
| `capability`               | string or null                | |
| `policy_version`           | string or null                | |
| `authority_state`          | enum `AuthorityState` or null | |
| `timestamp`                | datetime (UTC, tz-aware)      | from injected clock |
| `previous_state`           | enum `OperationState` or null | |
| `new_state`                | enum `OperationState` or null | |
| `receiver_response_class`  | enum `ReceiverResponseClass` or null | |
| `evidence_source`          | string                        | e.g. "dispatch-coordinator" |
| `payload`                  | dict or null                  | raw response / reason |
| `payload_digest`           | string or null                | reserved |
| `correlation_id`           | string or null                | groups a request chain |

Fields with `None` are stripped from the stored JSON before hashing.

## 3. Event types
OPERATION_CREATED, POLICY_CONFIGURED,
RELEASE_ISSUED, RELEASE_VERIFIED, RELEASE_VERIFICATION_FAILED,
RELEASE_CONSUMED, RELEASE_REPLAY_ATTEMPTED,
ADMISSIBILITY_ALLOWED, ADMISSIBILITY_DENIED,
AUTHORITY_REVOKED,
DISPATCH_ATTEMPTED, DISPATCH_ACCEPTED, DISPATCH_REJECTED,
IN_FLIGHT,
CANCELLATION_REQUESTED, CANCELLATION_ACKNOWLEDGED,
CANCELLATION_REJECTED, CANCELLATION_CONFIRMED,
COMPLETION_CONFIRMED, OUTCOME_UNKNOWN,
RECONCILIATION_ATTEMPTED, RECONCILIATION_RESOLVED, RECONCILIATION_INCONCLUSIVE,
RETRY_DECISION, GUARD_VIOLATED

text

## 4. Hash chain

### Construction
prev_hash = GENESIS_PREV_HASH ("")
for each event, in ascending sequence order:
payload_json = json.dumps(event_dict, sort_keys=True,
separators=(",", ":"), default=str)
hash = sha256( prev_hash_bytes || b"|" || payload_json_bytes ).hexdigest()
store = (sequence, event_id, event_type,
business_operation_id, payload_json, prev_hash, hash)
prev_hash = hash

text

- `sequence` is `AUTOINCREMENT`.
- `prev_hash` for the first event is the empty string, not a hash.
- The canonical JSON form is defined in `app/evidence/canonical.py`.

### What the hash chain **proves**

1. **Row-level tamper detection.** Modifying any stored `event_json` breaks
   the recomputed hash for that row and for every subsequent row.
2. **Ordering.** Any change to `sequence` ordering (deletion of a middle
   event, reordering, insertion) changes the `prev_hash` chain and is
   detected by recomputation.
3. **Cross-process consistency.** The verifier running in a separate
   process (CLI) reaches the same verdict as the API process, given the same
   database file.

### What the hash chain **does not prove**

1. **Full-database compromise.** An attacker with write access to the SQLite
   file can rewrite every row and recompute every hash. The result is a valid
   hash chain. **No local hash chain protects against this.** Anchoring the
   chain in an external, append-only store (WORM, transparency log, Merkle
   root publication to a third party) is required and is out of scope.
2. **Receiver truthfulness.** The chain proves what the *connector recorded*
   about the receiver's responses. It does not prove the receiver reported
   truthfully.
3. **Completeness of recording.** If a code path exists that mutates state
   without writing evidence, the chain will not detect the omission. The
   test suite has coverage for the paths in this repo, but there is no
   mechanical "all state changes emit evidence" enforcement.
4. **Timestamp accuracy.** Times come from the injected `Clock`; a malicious
   operator could configure a lying clock. The chain preserves whatever was
   recorded.

## 5. Verifier-only guarantees

The verifier enforces (in addition to chain validity):

- `no_guard_violations`: no `GUARD_VIOLATED` events.
- `single_business_operation`: every event has the expected
  `business_operation_id` (or none).
- `ascending_timestamps`: no time reversal within an operation.
- `release_verified_before_dispatch`: any `DISPATCH_ATTEMPTED` is preceded
  by `RELEASE_VERIFIED` for the same `release_id`.
- `release_consumed_before_dispatch_attempted`: any `DISPATCH_ACCEPTED` or
  `DISPATCH_REJECTED` is preceded by `RELEASE_CONSUMED` for the same
  `release_id`.
- `cancellation_confirmed_has_terminal_cancelled`: any
  `CANCELLATION_CONFIRMED` event carries `STATUS_TERMINAL_CANCELLED`.
- `completion_confirmed_has_terminal_completed`: any
  `COMPLETION_CONFIRMED` event carries `STATUS_TERMINAL_COMPLETED`.
- `retries_justified`: no `release_id` has more than one `DISPATCH_ATTEMPTED`
  event.

These are named and individually reported in `failed_assertions`.

## 6. Design decisions

- **Structured JSON, not free text.** Allows mechanical assertion.
- **Idempotent payload shape.** Adding new fields is a schema_version bump.
- **Single canonical form.** All writers use the same `build_event` +
  `to_storage_payload` path.
- **No pointer to receiver-internal state.** Only responses are recorded.
- **No customer PII.** Amounts and IDs are synthetic. No real payment data.

## 7. Tamper-detection test coverage

- `tests/test_evidence_ledger.py::test_tampering_with_event_payload_breaks_chain`
- `tests/test_evidence_ledger.py::test_deleting_middle_event_breaks_chain`
- `tests/test_verifier.py::test_tampered_ledger_causes_failure`
- `tests/scenarios/test_scenario_14.py::test_tampering_is_detected`

These are the tests that exercise the properties in §4 (what the chain
proves) and the invariant in §5. They do **not** test the residual risk in
§4 item 1 (full-database compromise), which is explicitly out of scope.
THREAT_MODEL.md

