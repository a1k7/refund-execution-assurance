# Refund Execution Assurance

A reference implementation of execution-boundary assurance and independent verification
for AI-initiated financial refunds, built as a joint technical evaluation between
**DecisionAssure** (this repo) and a **simulated Parmana** (signed release issuer).

> **This is not a production integration.** There is **no** Razorpay, Stripe, or
> any live payment connection. The external receiver is a local simulation whose
> internal ground truth is hidden from the verifier. All amounts, IDs, and keys
> are synthetic.

---

## 1. Project purpose

Demonstrate, through executable evidence, that:

1. A signed, single-use, epoch-scoped release can be consumed **atomically** at a
   local connector boundary.
2. Revocation of authority mid-flight can be enforced **at the receiver** via
   epoch fencing — not merely at the connector's pre-flight check.
3. Cancellation states are strictly separated: `CANCELLATION_REQUESTED`,
   `CANCELLATION_ACKNOWLEDGED`, `CANCELLATION_CONFIRMED` are distinct, and only
   the last is a terminal classification.
4. `OUTCOME_UNKNOWN` is a first-class state that blocks retries and requires
   reconciliation. It is never silently converted to success or failure.
5. An **independent verifier**, using only the recorded evidence ledger and
   with **no access** to the receiver's internal state, can reach a justified
   terminal conclusion — or declare that the terminal outcome is unknown.

## 2. Business problem

An AI agent is authorized to issue a customer refund. The refund is released,
checked, dispatched, and accepted. **Authority is then revoked while the refund
is in flight.** The system must determine — from evidence — whether the refund
completed, was cancelled, or remains unknown, and whether a retry could create
a duplicate.

The mandatory failure modes to prevent are:

- A cancellation *request* being treated as *confirmed cancellation*.
- An inconclusive receiver response being coerced into success or failure.
- A retry with a new idempotency key creating a second refund for the same
  business operation.
- Two concurrent dispatch workers both being accepted by the receiver.
- A stale release epoch (authority superseded) still being honoured.
- A verifier that "knows" something it cannot see in evidence.

## 3. Architecture at a glance
┌──────────────┐ signed release ┌──────────────────────────────┐
│ Simulated │ ────────────────▶ │ DecisionAssure (this repo) │
│ Parmana │ │ │
└──────────────┘ │ Release verifier │
│ Authority / revocation │
┌──────────────┐ revoke + epoch │ Atomic consumption │
│ Org authority│ ────────────────▶ │ Dispatch coordinator │
└──────────────┘ │ Cancellation service │
│ Reconciliation service │
┌──────────────┐ accept/cancel/ │ Evidence ledger (hash chain)│
│ Simulated │ status │ Independent verifier │
│ receiver │ ◀────────────────▶│ │
│ (fenced) │ │ FastAPI + Dashboard + CLI │
└──────────────┘ └──────────────────────────────┘
▲ │
│ test oracle reads ground truth │ verifier reads only evidence
│ (tests only, never in app/) ▼


The verifier never touches the receiver. The test oracle never touches the app.
See `ARCHITECTURE.md` for the full component diagram and
`THREAT_MODEL.md` for the trust boundaries.

## 4. Repository layout
refund-execution-assurance/
├── app/
│ ├── domain/ pure domain logic (enums, models, state machine, ids)
│ ├── release/ simulated Parmana issuer + cryptographic verifier
│ ├── authority/ revocation & epoch supersession service
│ ├── connector/ atomic consumption, duplicate guard, dispatch coordinator
│ ├── receiver/ receiver protocol, fencing registry, simulated receiver
│ ├── cancellation/ REQUESTED → ACKNOWLEDGED → CONFIRMED pipeline
│ ├── reconciliation/ OUTCOME_UNKNOWN resolution
│ ├── evidence/ hash-chained append-only ledger
│ ├── verification/ independent verifier + assertions + report
│ ├── persistence/ SQLite wrapper + schema + repositories
│ ├── api/ FastAPI routers
│ ├── demo/ 16 deterministic scenarios
│ ├── dashboard/ HTML/CSS/JS dashboard at /dashboard
│ └── main.py app factory
├── cli/verify.py offline verifier (reads a SQLite file directly)
├── scripts/ init_db.py, run_scenario.py
└── tests/ deterministic unit + scenario + concurrency tests


## 5. Requirements

- Python 3.11+
- macOS, Linux, or WSL (tested on macOS)
- The dependencies in `requirements.txt` (FastAPI, Pydantic, cryptography, pytest, httpx)
- No database server: the reference uses SQLite in WAL mode

## 6. Install and run (macOS)

```bash
cd refund-execution-assurance

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env          # optional: adjust paths
python scripts/init_db.py     # creates ./data/raa.db

# Start the API + dashboard
uvicorn app.main:app --reload --port 8000

Open:

Dashboard: http://127.0.0.1:8000/dashboard
OpenAPI docs: http://127.0.0.1:8000/docs
Root redirects to the dashboard

7. Run the tests
pytest -q

8. Run a scenario without the API

python scripts/run_scenario.py --list
python scripts/run_scenario.py 04

9. Verify a database offline
python -m cli.verify ./data/raa.db --ledger
python -m cli.verify ./data/raa.db bo_<business_operation_id>

The CLI does not start the API and does not import the receiver.

10. The 16 scenarios

ID	Summary
01	Revocation before dispatch
02	Revocation after issuance, before connector check
03	Revocation between check and receiver acceptance (fenced)
04	Revocation + cancellation confirmed
05	Completion before cancellation
06	Cancellation acknowledged, terminal unknown
07	Receiver completed internally, reported inconclusive
08	Default BLOCK_NEW_DISPATCHES policy
09	New idempotency key does not bypass duplicate guard
10	Concurrent dispatch: exactly one attempt wins
11	Replay of a consumed release
12	Expired / altered / wrong audience / wrong capability
13	Cancellation configured on incapable receiver
14	Evidence tampering detected
15	Reconciliation resolves OUTCOME_UNKNOWN
16	Stale release epoch rejected by connector and receiver
Full matrix in SCENARIO_MATRIX.md.

11. API summary

Method	Path	Purpose
POST	/release/operations	Create a business operation
POST	/release/issue	Issue a signed release (simulated Parmana)
GET	/release/{release_id}	Fetch a release
POST	/authority/revoke/{business_operation_id}	Revoke authority (or supersede epoch)
POST	/authority/policy/{business_operation_id}	Configure in-flight policy
POST	/dispatch	Dispatch a refund
POST	/cancellation/request	Request cancellation
POST	/cancellation/confirm-from-status	Query status to attempt confirmation
POST	/reconciliation/reconcile	Resolve OUTCOME_UNKNOWN
GET	/operations	List operations
GET	/operations/{business_operation_id}	Operation history + retry decision
GET	/evidence/{business_operation_id}	Evidence events for an operation
GET	/evidence/ledger/verify	Verify full chain integrity
GET	/verify/{business_operation_id}	Independent verification report
GET	/demo/scenarios	List available demo scenarios
POST	/demo/run	Run a demo scenario
GET	/dashboard	HTML dashboard
GET	/static/*	Dashboard assets
12. Configuration

Environment variables (all optional, defaults shown):

text
RAA_ENV=dev
RAA_LOG_LEVEL=INFO
RAA_DB_PATH=./data/raa.db
RAA_PARMANA_KEY_ID=parmana-demo-key-1
RAA_PARMANA_PRIVATE_KEY_PATH=./data/parmana_private.pem
RAA_PARMANA_TRUST_STORE_PATH=./data/parmana_trust.json
RAA_RECEIVER_DEFAULT_BEHAVIOR=normal
The private key PEM is generated on first use and stored on disk. The trust
store is a JSON file. This is a developer convenience, not a production
key-management story. See THREAT_MODEL.md §3.

13. What this repo is not

Not a production integration with Razorpay, Stripe, or any payment rail.
Not a distributed transaction. Local SQLite guarantees at most one local
winner per release. External effect is protected only by receiver fencing
whose freshness depends on propagation latency.
Not a substitute for a real KMS/HSM. Signing keys live on disk.
Not a defence against full database compromise. The hash chain detects
row-level tampering and reordering; an attacker who can rewrite every
row and recompute every hash will produce a valid chain.
Not a claim that Parmana has a particular API. All Parmana-shaped
interfaces are labelled as assumptions in INTEGRATION_CONTRACT.md.
14. Documentation index

ARCHITECTURE.md — components, single-process limits, two-layer defense
STATE_MACHINE.md — every legal transition, invariants
EVIDENCE_SPEC.md — event schema, hash chain, what it proves / doesn't
THREAT_MODEL.md — assets, attackers, mitigations, residual risk
INTEGRATION_CONTRACT.md — Parmana interface, all assumptions labeled
SCENARIO_MATRIX.md — 16-scenario grid: ground truth, evidence, verdict, test
15. License

Reference implementation only. No warranty. See the repository root.