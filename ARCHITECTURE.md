```markdown
# Architecture

## 1. Component map

```mermaid
flowchart LR
    subgraph EXT["Simulated external surface"]
        P["Parmana Release Issuer<br/>(Ed25519, local PEM key)"]
        A["Authority / Revocation<br/>(source of truth)"]
        R["Simulated Receiver<br/>(accept / cancel / status)<br/>+ fencing registry"]
    end

    subgraph DA["DecisionAssure (this repo)"]
        RV["Release Verifier<br/>(signature, audience,<br/>capability, expiry, subject)"]
        DG["Duplicate Guard<br/>(business_operation_id)"]
        AC["Atomic Consumption<br/>(BEGIN IMMEDIATE + conditional UPDATE)"]
        DC["Dispatch Coordinator<br/>(per-operation mutex)"]
        CS["Cancellation Service<br/>(REQUESTED/ACK/CONFIRMED)"]
        RS["Reconciliation Service"]
        L["Evidence Ledger<br/>(SQLite hash chain)"]
        V["Independent Verifier<br/>(reads ledger only)"]
        API["FastAPI + Dashboard + CLI"]
    end

    P -- "SignedRelease" --> DC
    A -- "revoke + epoch bump" --> DC
    A -. "propagate epoch" .-> R
    DC --> RV
    DC --> DG
    DC --> AC
    DC -- "accept(epoch, ...)" --> R
    CS -- "cancel / query_status" --> R
    RS -- "query_status" --> R
    DC --> L
    CS --> L
    RS --> L
    L --> V
    DC --> API
    CS --> API
    RS --> API
    V --> API

    2. Two-layer dispatch defense

This is the central design property of the system.

Layer 1 — Local atomic release consumption

Executed inside a single SQLite BEGIN IMMEDIATE transaction.
Implemented as a conditional UPDATE ... WHERE consumed_at IS NULL.
Guarantees: at most one local worker can consume a given release_id.
Does not guarantee that the external receiver accepted the request.
Layer 2 — Receiver-side epoch fencing

The receiver owns an authoritative per-operation current_epoch.
On each accept(...), it rejects any request whose release_epoch is
strictly less than its current epoch.
The epoch is bumped by the authority service and propagated to the receiver
via update_fencing_state.
Guarantees (conditional on freshness): a stale release cannot cause a
refund at the receiver, even if the connector's pre-flight check was passed
before the epoch was bumped.
Why both are required

A worker may pass the connector's admissibility check and then lose the race
against a revocation (see scenario 3). The connector's local view of authority
is stale at the moment the receiver is called. Only the receiver can enforce
its own freshness.

Conversely, if only receiver fencing existed and two workers raced to the same
receiver with the same release, both could be accepted (assuming the receiver
has not yet moved its epoch). Atomic local consumption ensures only one request
reaches the receiver.

Honest scope of the "atomic" claim: local SQLite atomicity is local.
There is no distributed transaction spanning DecisionAssure and the
receiver. The two sides are protected by two distinct mechanisms with
different failure modes. See §6.

3. Verifier isolation

The verifier (app/verification/verifier.py) has exactly one dependency:

text
EvidenceLedgerReader  (protocol, in app/evidence/storage.py)
It does not import:

app.receiver.*
app.connector.* (except read-only ledger)
app.persistence.repositories.OperationRepository
any module whose name contains "oracle"
This is enforced by two tests:

test_security_negative.py::test_verifier_module_has_no_imports_from_receiver_package
test_security_negative.py::test_app_does_not_reference_receiver_oracle
A verifier report includes:

verification_status: VERIFIED / FAILED / INCONCLUSIVE
terminal_classification: COMPLETION_CONFIRMED / CANCELLATION_CONFIRMED / OUTCOME_UNKNOWN / null
evidence_events_examined
failed_assertions (named)
warnings
unresolved_conditions
retry_eligible, reconciliation_required
chain_valid
The verifier cannot fabricate a terminal classification. If the last
terminal event in the ledger does not carry the appropriate receiver_response_class
(e.g. STATUS_TERMINAL_COMPLETED for a COMPLETION_CONFIRMED event),
the verifier sets verification_status = FAILED. This is the strongest
available defence against a compromised workflow engine trying to lie about
the outcome in its own ledger.

4. Ground truth vs. reported vs. recorded vs. verified

The system distinguishes four epistemic layers:

Layer	Source	Visible to
Ground truth	Receiver's internal _refunds state	Receiver, test oracle only
Reported	Receiver response payload	Connector, cancellation service
Recorded	Evidence ledger event	Verifier, API, CLI, dashboard
Verified	Independent verifier conclusion	Everyone
Verifier invariant: the verifier reasons only over recorded evidence.
If the receiver internally completed but returned INCONCLUSIVE (scenario 7),
then:

Ground truth: COMPLETED.
Reported: INCONCLUSIVE.
Recorded: OUTCOME_UNKNOWN evidence event, no terminal evidence.
Verified: OUTCOME_UNKNOWN.
The test oracle reads ground truth separately to prove the verifier does not
infer it. The oracle is not importable from app/.

5. Persistence & transactions

One SQLite database per process, WAL mode, foreign keys ON.
A single threading.Lock serializes write transactions at the application
level (see the corrected app/persistence/db.py). The BEGIN IMMEDIATE
is retained for defence in depth.
Reads are not locked; WAL gives consistent snapshots.
Single-process only. Multi-process deployment requires a different
store (e.g. PostgreSQL with SELECT ... FOR UPDATE and an advisory lock).
6. What "atomic" does not mean here

The atomicity guarantee is per local database transaction, not across
the receiver boundary.

If the connector consumes the release and then crashes before the receiver
is called:

The release is consumed in the local DB.
No external refund exists.
Retry is blocked (release is already consumed).
Reconciliation cannot help — there is no receiver reference to query.
This is a known limitation. A production system would either (a) hold the
consumption in a pending state until the receiver call returns or (b) use an
outbox pattern with delivery guarantees. Both are out of scope for this
reference implementation and are documented as such.

7. Integration adapter boundary

app/receiver/protocol.py defines the ReceiverProtocol:

python
class ReceiverProtocol(Protocol):
    @property
    def capabilities(self) -> ReceiverCapabilities: ...
    def accept(self, ...) -> ReceiverResponse: ...
    def cancel(self, ...) -> ReceiverResponse: ...
    def query_status(self, ...) -> ReceiverResponse: ...
    def update_fencing_state(self, business_operation_id, current_epoch) -> None: ...
Real integration would replace SimulatedRefundReceiver with an adapter that
calls Parmana (or a payment provider) — provided Parmana exposes:

a signed release verification surface (or a portable artifact that
DecisionAssure can verify offline)
a fencing identifier per operation
a cancellation operation that is distinct from its acknowledgment
a status-query mechanism that can return a terminal state
If Parmana cannot provide either cancellation or status query, the
REQUEST_CANCELLATION_OF_IN_FLIGHT policy must not be enabled — enforced by
CancellationService.validate_receiver_capabilities().

8. Assumptions requiring Parmana confirmation

See INTEGRATION_CONTRACT.md §"Assumptions" for the numbered list. Summary:

Signed release schema and canonical form
Signature algorithm and key handling
Trust provisioning and rotation
Fencing primitive (epoch) semantics
Revocation propagation freshness
Release validity window and clock skew policy
Single-use enforcement location (issuer vs. DecisionAssure)
Business operation ID provenance