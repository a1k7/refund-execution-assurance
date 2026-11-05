```markdown
# State machine

Implementation: `app/domain/state_machine.py` (pure, no I/O, no side effects).
Every state transition in the codebase funnels through `assert_transition`.

## 1. States

| State                     | Meaning                                                              |
|---------------------------|----------------------------------------------------------------------|
| `CREATED`                 | Business operation exists; no release yet                            |
| `RELEASE_ISSUED`          | A signed release exists for this operation                           |
| `RELEASE_VERIFIED`        | Release cryptographic + policy checks passed                         |
| `ADMISSIBILITY_DENIED`    | Release valid but authority/epoch denies dispatch                    |
| `ADMISSIBILITY_ALLOWED`   | Pre-flight checks passed, ready to dispatch                          |
| `RELEASE_CONSUMED`        | Atomic consumption succeeded (single-use reservation taken)          |
| `DISPATCH_ATTEMPTED`      | Dispatch attempt is in progress, receiver call pending               |
| `DISPATCH_REJECTED`       | Receiver explicitly rejected the request                             |
| `DISPATCH_ACCEPTED`       | Receiver accepted; refund now exists at receiver                     |
| `IN_FLIGHT`               | Refund exists at receiver, no terminal state yet                     |
| `CANCELLATION_REQUESTED`  | Cancel request has been issued to the receiver                       |
| `CANCELLATION_ACKNOWLEDGED` | Receiver acknowledged the cancel request (NOT proof of cancellation) |
| `CANCELLATION_CONFIRMED`  | Terminal: receiver has independently confirmed cancellation           |
| `COMPLETION_CONFIRMED`    | Terminal: receiver has independently confirmed completion            |
| `OUTCOME_UNKNOWN`         | Terminal outcome cannot be independently established                 |

Two further dimensions are stored on the operation record:

- `AuthorityState`: `ACTIVE` | `REVOKED` (independent of `OperationState`)
- `InFlightPolicy`: `BLOCK_NEW_DISPATCHES` | `REQUEST_CANCELLATION_OF_IN_FLIGHT`

## 2. Legal transitions
CREATED ─────────────────────────▶ RELEASE_ISSUED
RELEASE_ISSUED ────────────┬────▶ RELEASE_VERIFIED
└────▶ ADMISSIBILITY_DENIED
RELEASE_VERIFIED ──────────┬────▶ ADMISSIBILITY_ALLOWED
└────▶ ADMISSIBILITY_DENIED
ADMISSIBILITY_ALLOWED ─────┬────▶ RELEASE_CONSUMED
└────▶ ADMISSIBILITY_DENIED
ADMISSIBILITY_DENIED ──────┬────▶ RELEASE_ISSUED (retry with new release)
└────▶ OUTCOME_UNKNOWN
RELEASE_CONSUMED ──────────────▶ DISPATCH_ATTEMPTED
DISPATCH_ATTEMPTED ────────┬────▶ DISPATCH_ACCEPTED
├────▶ DISPATCH_REJECTED
└────▶ OUTCOME_UNKNOWN
DISPATCH_REJECTED ─────────┬────▶ RELEASE_ISSUED (retry, subject to duplicate guard)
└────▶ OUTCOME_UNKNOWN
DISPATCH_ACCEPTED ─────────┬────▶ IN_FLIGHT
├────▶ CANCELLATION_REQUESTED
├────▶ COMPLETION_CONFIRMED
└────▶ OUTCOME_UNKNOWN
IN_FLIGHT ─────────────────┬────▶ CANCELLATION_REQUESTED
├────▶ COMPLETION_CONFIRMED
└────▶ OUTCOME_UNKNOWN
CANCELLATION_REQUESTED ────┬────▶ CANCELLATION_ACKNOWLEDGED
├────▶ CANCELLATION_CONFIRMED
├────▶ COMPLETION_CONFIRMED
└────▶ OUTCOME_UNKNOWN
CANCELLATION_ACKNOWLEDGED ─┬────▶ CANCELLATION_CONFIRMED
├────▶ COMPLETION_CONFIRMED
└────▶ OUTCOME_UNKNOWN
OUTCOME_UNKNOWN ───────────┬────▶ COMPLETION_CONFIRMED
├────▶ CANCELLATION_CONFIRMED
└────▶ OUTCOME_UNKNOWN (self-reentry)
COMPLETION_CONFIRMED (terminal, no outgoing)
CANCELLATION_CONFIRMED (terminal, no outgoing)


The self-transition `OUTCOME_UNKNOWN → OUTCOME_UNKNOWN` is the *only* legal
self-transition. It models repeated reconciliation attempts that remain
inconclusive without violating the "no phantom transitions" rule.

## 3. Invariants (enforced at write time)

1. **Release before dispatch.** A `DISPATCH_ATTEMPTED` event must be preceded
   by a `RELEASE_VERIFIED` event for the same `release_id`.
   *Checked by the verifier (defence in depth).*
2. **Consumption before dispatch outcome.** A `DISPATCH_ACCEPTED` or
   `DISPATCH_REJECTED` event must be preceded by `RELEASE_CONSUMED` for the
   same `release_id`.
3. **Cancel must be acknowledged or terminal, never confirmed silently.**
   `CANCELLATION_CONFIRMED` events must carry
   `receiver_response_class == STATUS_TERMINAL_CANCELLED`.
4. **Completion must be evidenced.** `COMPLETION_CONFIRMED` events must carry
   `receiver_response_class == STATUS_TERMINAL_COMPLETED`.
5. **Guard violations are visible.** Every illegal transition attempted by
   code is recorded as a `GUARD_VIOLATED` evidence event, and the verifier
   treats any such event as a failure.

## 4. State-independent dimensions

- `authority_state` may flip to `REVOKED` at any time. It is **not** a state
  in the state machine; it is a gate for new dispatches.
- `in_flight_policy` is per-operation configuration.
- `current_epoch` is monotonic and bumped by `revoke` or `supersede`.
- `terminal_classification` mirrors the terminal state for easy querying:
  `COMPLETION_CONFIRMED`, `CANCELLATION_CONFIRMED`, or `OUTCOME_UNKNOWN`.

## 5. Sequences

### Dispatch (scenario 1/2 — denial)

```mermaid
sequenceDiagram
    participant C as Connector
    participant A as Authority
    participant L as Ledger
    C->>A: is_revoked?
    A-->>C: true
    C->>L: ADMISSIBILITY_DENIED
    Note over C: state = ADMISSIBILITY_DENIED