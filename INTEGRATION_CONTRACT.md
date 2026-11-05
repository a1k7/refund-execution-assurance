# Integration contract with Parmana

> **Status: all Parmana-shaped interfaces in this repo are local simulations.**
> This document lists every field, behaviour, and assumption that must be
> confirmed by Parmana before any real integration is attempted.

## 1. Input from Parmana

DecisionAssure requires the following from a Parmana signed release:

| Field                    | Type                | Assumption label |
|--------------------------|---------------------|------------------|
| `release_id`             | string              | A-1              |
| `release_epoch`          | integer (monotonic) | A-2              |
| `business_operation_id`  | string              | A-3              |
| `subject` (agent id)     | string              | A-4              |
| `audience`               | string              | A-5              |
| `capability`             | string              | A-5              |
| `policy_version`         | string              | A-6              |
| `issued_at`              | RFC3339 (UTC)       | A-7              |
| `expires_at`             | RFC3339 (UTC)       | A-7              |
| `amount`                 | integer (minor units)| A-8             |
| `currency`               | ISO 4217            | A-8              |
| `params_digest`          | string              | A-9              |
| `signature.key_id`       | string              | A-10             |
| `signature.alg`          | "Ed25519" (current) | A-10             |
| `signature.signature_b64`| base64              | A-10             |

The canonical byte form that gets signed is defined in
`app/release/canonical.py`. **Assumption A-11**: Parmana's canonical form may
differ (JCS, protobuf, fixed field ordering, etc.). If so, this is the
single file that needs to change to match, plus the corresponding field
serialization in `ReleasePayload`.

## 2. Expected integration behaviour

| Step | What DecisionAssure does                                | What Parmana must provide |
|------|----------------------------------------------------------|--------------------------|
| 1    | Verify signature against trust store                    | Trust provisioning out of band (A-10) |
| 2    | Verify `audience` and `capability`                      | Stable vocabularies (A-5) |
| 3    | Verify expiry against DecisionAssure clock              | Clock skew policy (A-7)  |
| 4    | Check single-use status                                 | Either Parmana enforces single-use at issuance, or DecisionAssure does (A-2) |
| 5    | Check authority/revocation state                        | Revocation channel (A-12) |
| 6    | Enforce atomic consumption                              | (local) |
| 7    | Enforce receiver-side fencing                           | Receiver must expose an authoritative, fresh fencing state (A-13) |
| 8    | Record the release reference in evidence                | (local) |

## 3. Output to Parmana

| Field                       | Where it originates                        |
|-----------------------------|--------------------------------------------|
| Admissibility result        | `ADMISSIBILITY_ALLOWED` / `ADMISSIBILITY_DENIED` evidence |
| `dispatch_attempt_id`       | `app.domain.ids.new_dispatch_attempt_id`   |
| Execution state             | `OperationState`                           |
| Cancellation state          | `OperationState` + `CancellationService`   |
| `receiver_refund_reference` | Receiver response                          |
| Terminal classification     | `TerminalClassification`                   |
| Evidence bundle             | `/evidence/{business_operation_id}`        |
| Verification report         | `/verify/{business_operation_id}` + `cli.verify` |

## 4. Assumptions (explicit, each requiring Parmana confirmation)

- **A-1.** `release_id` is globally unique and stable.
- **A-2.** `release_epoch` is monotonic per `business_operation_id` and is
  the correct fencing identifier. Whether Parmana also enforces single-use
  at issuance is TBD.
- **A-3.** `business_operation_id` is stable for the lifetime of the refund
  and is the correct unit of duplicate prevention.
- **A-4.** `subject` uniquely identifies the AI agent that requested the
  refund (or the composite agent+process).
- **A-5.** `audience` and `capability` are stable string identifiers agreed
  in advance with DecisionAssure. Example values used here:
  `audience = "decisionassure:refunds"`, `capability = "refund.issue"`.
- **A-6.** `policy_version` is a stable identifier of the policy under which
  this release was issued.
- **A-7.** `issued_at`/`expires_at` use UTC. Clock skew handling is TBD;
  the reference implementation rejects releases that are not-yet-valid on
  the local clock.
- **A-8.** `amount` is in minor units; `currency` is ISO 4217.
- **A-9.** `params_digest` binds the release to specific refund parameters
  (account, amount, memo). Its exact derivation is TBD.
- **A-10.** Signature is Ed25519; `key_id` indexes into a trust store. Key
  rotation and revocation policy are TBD.
- **A-11.** The canonical byte form for signing matches
  `app/release/canonical.py`. **If it doesn't, the trust boundary breaks
  silently.** This must be confirmed with a known test vector from Parmana.
- **A-12.** Revocation is discoverable by DecisionAssure before dispatch. In
  this repo, revocation is a local service; in production it will be a call
  to Parmana or an authority service.
- **A-13.** The receiver's fencing state is authoritative and fresh.
  **Propagation freshness is TBD.** The system must state a maximum
  acceptable staleness window and refuse to operate when it cannot verify
  it. In the reference implementation, propagation is synchronous.

## 5. What this repo does **not** do

- Does not contact Parmana over any network.
- Does not use any Parmana API endpoints.
- Does not consume any Parmana-specific SDK.
- Does not claim that its local schema matches Parmana's real schema.
- Does not use real customer data.

## 6. Steps to connect a real Parmana

1. Obtain a sample signed release from Parmana.
2. Compare `app/release/canonical.py` with Parmana's canonical form.
   Adjust if needed; write a known-answer test with the sample.
3. Implement a real `TrustStore` (backed by Parmana's trust distribution).
4. Implement a real `RevocationService.revoke` that queries Parmana's
   revocation endpoint or consumes its revocation event stream.
5. Implement a real receiver adapter that satisfies `ReceiverProtocol` and
   documents its fencing propagation latency.
6. Re-run the 16 scenario tests against the real adapter in a staging
   environment.
7. Update `THREAT_MODEL.md` §3.4 and §3.5 with the real receiver's
   guarantees.