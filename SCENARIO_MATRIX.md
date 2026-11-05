# Scenario matrix

All 16 required scenarios are implemented as end-to-end tests under
`tests/scenarios/`. The reference demo runner in `app/demo/scenarios.py`
mirrors each test for the dashboard and the `/demo/run` endpoint.

Column legend:
- **Ground truth** — what the *simulated receiver* actually did (test oracle).
- **Evidence visible to verifier** — the terminal-evidence pattern that
  the verifier sees.
- **Expected classification** — what the verifier concludes.
- **Retry** — what the duplicate guard / retry policy decides.
- **Recon.** — whether reconciliation is required.
- **Test** — `file::function` (all under `tests/`).

| # | Initial state | Event sequence | Receiver ground truth | Evidence visible to verifier | Expected classification | Retry | Recon. | Test |
|---|---------------|----------------|-----------------------|------------------------------|-------------------------|-------|--------|------|
| 01 | CREATED, no release | issue → revoke → dispatch | no refund created | `ADMISSIBILITY_DENIED`, no `DISPATCH_*` | none (not terminal; retry would be blocked by authority revoked) | BLOCK_AUTHORITY_REVOKED | No | `scenarios/test_scenario_01.py::test_revocation_before_dispatch_blocks` |
| 02 | RELEASE_ISSUED | issue → revoke → dispatch | no refund | `ADMISSIBILITY_DENIED` | none | BLOCK_AUTHORITY_REVOKED | No | `scenarios/test_scenario_02.py::test_revocation_after_issue_before_connector_check` |
| 03 | IN_FLIGHT-eligible | issue → hook revoke mid-dispatch → dispatch | no refund (receiver fenced) | `RELEASE_CONSUMED`, `AUTHORITY_REVOKED`, `DISPATCH_REJECTED` w/ `REJECTED_FENCED` | none | ALLOW (previous attempt DISPATCH_REJECTED) | No | `scenarios/test_scenario_03.py::test_revocation_between_check_and_acceptance_is_fenced` |
| 04 | IN_FLIGHT | dispatch → revoke → cancel → status | refund CANCELLED | `CANCELLATION_CONFIRMED` w/ `STATUS_TERMINAL_CANCELLED` | CANCELLATION_CONFIRMED | BLOCK_REQUIRE_NEW_AUTHORIZATION | No | `scenarios/test_scenario_04.py::test_revocation_after_dispatch_cancellation_confirmed` |
| 05 | IN_FLIGHT | dispatch (complete) → revoke → cancel rejected | refund COMPLETED | `COMPLETION_CONFIRMED` w/ `STATUS_TERMINAL_COMPLETED` | COMPLETION_CONFIRMED | BLOCK_COMPLETION_CONFIRMED | No | `scenarios/test_scenario_05.py::test_completion_before_cancellation_wins` |
| 06 | CANCELLATION_ACKNOWLEDGED | dispatch → revoke → cancel ack → status unknown | refund CANCELLED (internal) | `OUTCOME_UNKNOWN` (no terminal evidence) | OUTCOME_UNKNOWN | BLOCK_REQUIRE_RECONCILIATION | Yes | `scenarios/test_scenario_06.py::test_cancellation_ack_with_unknown_terminal` |
| 07 | OUTCOME_UNKNOWN | dispatch (inconclusive) | refund COMPLETED (hidden) | `OUTCOME_UNKNOWN` | OUTCOME_UNKNOWN | BLOCK_REQUIRE_RECONCILIATION | Yes | `scenarios/test_scenario_07.py::test_verifier_does_not_infer_hidden_completion` |
| 08 | IN_FLIGHT | dispatch → revoke (default policy) | refund IN_FLIGHT | `AUTHORITY_REVOKED`, no cancel events | none (still IN_FLIGHT) | BLOCK_IN_FLIGHT | No | `scenarios/test_scenario_08.py::test_default_policy_leaves_in_flight_operation_untouched` |
| 09 | OUTCOME_UNKNOWN | dispatch (inconclusive) → retry w/ new idem key | 0 refunds | `RETRY_DECISION` (BLOCK_REQUIRE_RECONCILIATION) | OUTCOME_UNKNOWN | BLOCK_REQUIRE_RECONCILIATION | Yes | `scenarios/test_scenario_09.py::test_new_idempotency_key_does_not_open_new_slot` |
| 10 | ADMISSIBILITY_ALLOWED | 2 concurrent dispatch (Barrier) | exactly 1 refund | 1× `RELEASE_CONSUMED`, ≤1× `DISPATCH_ACCEPTED`, other `RELEASE_REPLAY_ATTEMPTED` | IN_FLIGHT | BLOCK_IN_FLIGHT | No | `scenarios/test_scenario_10.py::test_concurrent_dispatch_yields_one_accepted_attempt` |
| 11 | DISPATCH_REJECTED | dispatch(rejected) → replay same release | 0 refunds | `RELEASE_REPLAY_ATTEMPTED` | none (DISPATCH_REJECTED) | ALLOW (previous rejected) | No | `scenarios/test_scenario_11.py::test_replay_of_consumed_release_is_rejected` |
| 12 | RELEASE_ISSUED | 5× invalid dispatch | 0 refunds | 5× `RELEASE_VERIFICATION_FAILED` | none | ALLOW | No | `scenarios/test_scenario_12.py` (5 tests) |
| 13 | IN_FLIGHT, capable=False | dispatch → revoke → attempt cancel | 1 refund IN_FLIGHT | no `CANCELLATION_REQUESTED`; `ReceiverCapabilityError` raised | none | ALLOW | No | `scenarios/test_scenario_13.py::test_cancellation_config_rejected_when_receiver_lacks_capability` |
| 14 | Any | tamper with an evidence row | n/a | chain invalid | FAILED (`chain_invalid` failed assertion) | n/a | n/a | `scenarios/test_scenario_14.py::test_tampering_is_detected` |
| 15 | OUTCOME_UNKNOWN | dispatch (inconclusive, completed internally) → reconcile | refund COMPLETED | `RECONCILIATION_RESOLVED` w/ `STATUS_TERMINAL_COMPLETED` | COMPLETION_CONFIRMED | BLOCK_COMPLETION_CONFIRMED | No (after) | `scenarios/test_scenario_15.py::test_reconciliation_resolves_unknown_to_completion` |
| 16 | RELEASE_ISSUED (epoch 0) | issue(epoch=0) → supersede → dispatch(epoch=0) | 0 refunds | `ADMISSIBILITY_DENIED` (stale_epoch) + receiver `REJECTED_FENCED` | none | ALLOW | No | `scenarios/test_scenario_16.py::test_stale_epoch_rejected_by_connector` |

## Retry decision reference

`RetryDecisionKind` values observed in the matrix:

- `ALLOW` — previous attempt was rejected or no attempt exists; guard
  permits a new dispatch attempt (subject to authority / release validity).
- `BLOCK_IN_FLIGHT` — the operation is currently IN_FLIGHT / DISPATCH_ACCEPTED
  / CANCELLATION_REQUESTED / CANCELLATION_ACKNOWLEDGED.
- `BLOCK_REQUIRE_RECONCILIATION` — previous attempt is OUTCOME_UNKNOWN.
- `BLOCK_COMPLETION_CONFIRMED` — a refund already completed.
- `BLOCK_REQUIRE_NEW_AUTHORIZATION` — a refund was cancelled; a new refund
  would be a new business decision.
- `BLOCK_AUTHORITY_REVOKED` — authority is revoked and no new dispatch is
  permitted.

## Coverage notes

Every one of the 16 required scenarios from the prompt is implemented as a
named test under `tests/scenarios/`. Every scenario has:
- deterministic inputs (no sleeps, no randomness, injected clock),
- an assertion against the *verifier's* conclusion, and
- where meaningful, an assertion against *receiver ground truth* via the
  test-only `ReceiverOracle`.

The test oracle is deliberately isolated from `app/`. See
`tests/fixtures/receiver_oracle.py` and `tests/test_security_negative.py::
test_app_does_not_reference_receiver_oracle`.

## What is *not* covered

- Multi-process concurrency (out of scope; single-process SQLite model).
- Receiver-side bugs beyond the simulation.
- Full-database compromise (documented limitation; hash chain detects
  row-level tampering only).
- Network-level attacks on the receiver transport (out of scope).
- Clock tampering (out of scope; documented in `THREAT_MODEL.md` §3.9).