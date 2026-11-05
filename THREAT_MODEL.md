# Threat model

## 1. Assets

| Asset                         | Sensitivity   | Where it lives          |
|-------------------------------|---------------|-------------------------|
| Refund amount & currency      | High          | Evidence payloads       |
| Business operation identity   | Medium        | SQLite                  |
| Signed release artifact       | High          | SQLite + evidence       |
| Private signing key           | Critical      | Local PEM on disk       |
| Receiver's refund state       | High          | Receiver memory only    |
| Evidence ledger               | High          | SQLite (WAL)            |
| Authority state               | Critical      | SQLite + revocation log |

## 2. Attacker profiles

1. **Compromised AI agent.** Holds a valid release but tries to issue
   multiple refunds or replay a consumed release.
2. **Malicious concurrent worker.** A second process/thread attempts to
   dispatch the same release or the same operation in parallel.
3. **Compromised workflow engine.** Attempts to fabricate terminal evidence
   (e.g. write a `COMPLETION_CONFIRMED` event that isn't backed by
   receiver evidence).
4. **Operator with DB write access.** Attempts to rewrite evidence history.
5. **Compromised receiver.** Returns fabricated status responses.
6. **Network attacker.** Intercepts or replays dispatch calls.
7. **Insider with filesystem access.** Reads the signing key.

## 3. Mitigations and residual risk

### 3.1 Duplicate refund (attacker 1, 2)

- Duplicate guard keyed on `business_operation_id`.
- Atomic release consumption: one winner per release.
- Per-operation mutex in `DispatchCoordinator` (single-process).
- `OUTCOME_UNKNOWN` blocks all retries.
- **Residual:** multi-process deployment without a distributed lock would
  require re-validation. The documented single-process model is the scope.

### 3.2 Replay of a consumed release (attacker 1, 6)

- Local consumption check rejects already-consumed releases.
- Receiver-side fencing rejects stale epochs.
- Every replay attempt is recorded as `RELEASE_REPLAY_ATTEMPTED`.
- **Residual:** none for the local model; a distributed model would need
  distributed single-use tracking.

### 3.3 Revocation race (attacker 2, 5)

- Scenario 3 shows the connector's check is not sufficient.
- Receiver fencing rejects stale epochs.
- **Trust assumption:** the receiver's fencing state is authoritative and
  fresh. Propagation latency is synchronous in this reference implementation.
  In production, freshness bounds must be stated and enforced.
- **Residual:** if the receiver accepts an epoch bump after accepting a
  dispatch, the dispatch was legitimate at the moment of acceptance. The
  system must then treat the refund as in-flight and require reconciliation
  to establish outcome.

### 3.4 Fabricated terminal evidence (attacker 3)

- Verifier asserts that `COMPLETION_CONFIRMED` / `CANCELLATION_CONFIRMED`
  events carry a matching `receiver_response_class`.
- If a compromised engine writes these events without the correct class, the
  verifier returns `FAILED`.
- **Residual:** the engine could forge the `receiver_response_class` value
  too. Only full-database compromise (§3.6) or an external witness can
  defeat a determined forger with write access. This is documented in
  `EVIDENCE_SPEC.md` §4.

### 3.5 Cancellation ack treated as confirmation (all)

- `CancellationService` requires an explicit `query_status` returning
  `STATUS_TERMINAL_CANCELLED` to emit `CANCELLATION_CONFIRMED`.
- If the receiver cannot support both cancellation and status query, the
  service refuses to operate — the `REQUEST_CANCELLATION_OF_IN_FLIGHT`
  policy is effectively disabled.
- **Residual:** none for the reference implementation.

### 3.6 Evidence rewrite (attacker 4)

- Hash chain detects row-level tampering and reordering.
- **Residual:** an attacker with full DB write access and enough time can
  recompute every hash. External anchoring (WORM, transparency log, Merkle
  root publication) is required and out of scope. This is stated as a
  limitation, not claimed as protected.

### 3.7 Receiver fabrication (attacker 5)

- Evidence records *what was reported*; the verifier reasons over that.
- If the receiver lies consistently, the verifier cannot detect the lie.
- **Residual:** out of scope. The verifier's contract is "justified
  conclusion from evidence", not "external truth enforcement."

### 3.8 Signing key compromise (attacker 7)

- Private key stored as PEM on disk with default file permissions.
- Trust store is a JSON file.
- **Residual:** an attacker with filesystem read access can sign arbitrary
  releases. Production requires HSM/KMS with rotation and revocation. See
  `INTEGRATION_CONTRACT.md` §"Key handling assumptions".

### 3.9 Clock manipulation

- All timestamps come from the injected `Clock`.
- The default `SystemClock` returns `datetime.now(timezone.utc)`.
- **Residual:** an attacker who can change the system clock can influence
  expiry checks and event timestamps. Out of scope for this reference.

## 4. Non-threats (explicitly out of scope)

- Distributed deployment, cluster coordination, consensus
- Payment-provider authentication, PCI scope
- Network-level TLS termination, service mesh
- Multi-tenant isolation
- Rate limiting, abuse prevention
- GDPR / data-subject rights

## 5. Deployment assumptions

- One process, one SQLite database file, one set of signing keys.
- All components share the process's `Clock` and `Database`.
- `app/` does not import the test oracle or any test-only module.

## 6. Audit hooks

- Every significant transition emits a structured evidence event.
- `GUARD_VIOLATED` events surface illegal-transition attempts.
- The verifier reports named failed assertions.
- `cli/verify.py` reads the database directly, without the API or receiver.