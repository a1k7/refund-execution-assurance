async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json();
}

function badgeClass(value) {
  const map = {
    ALLOW: "ok", VERIFIED: "ok", ACCEPTED: "ok",
    COMPLETION_CONFIRMED: "ok", CANCELLATION_CONFIRMED: "ok",
    DENY: "bad", FAILED: "bad", REJECTED_FENCED: "bad",
    OUTCOME_UNKNOWN: "warn", INCONCLUSIVE: "warn",
    CANCELLATION_REQUESTED: "warn", CANCELLATION_ACKNOWLEDGED: "warn",
    RECONCILIATION_REQUIRED: "warn", IN_FLIGHT: "info",
  };
  return map[value] || "neutral";
}

function badge(value) {
  if (value === null || value === undefined) return '<span class="badge neutral">—</span>';
  return `<span class="badge ${badgeClass(value)}">${value}</span>`;
}

async function loadScenarios() {
  const list = await api("/demo/scenarios");
  const container = document.getElementById("scenarios");
  container.innerHTML = "";
  for (const s of list) {
    const btn = document.createElement("button");
    btn.textContent = `${s.scenario_id}. ${s.description}`;
    btn.onclick = () => runScenario(s.scenario_id);
    container.appendChild(btn);
  }
}

async function runScenario(id) {
  const resultEl = document.getElementById("result");
  resultEl.innerHTML = `<span class="spinner">Running ${id}…</span>`;
  try {
    const r = await api("/demo/run", { method: "POST", body: JSON.stringify({ scenario_id: id }) });
    renderResult(r);
    const ev = await api(`/evidence/${r.business_operation_id}`);
    renderTimeline(ev);
  } catch (e) {
    resultEl.innerHTML = `<div class="error">${e.message}</div>`;
  }
}

function renderResult(r) {
  const op = r.operation;
  const v = r.verification_report;
  const el = document.getElementById("result");
  el.innerHTML = `
    <div class="result-grid">
      <div><label>Scenario</label><div>${r.scenario_id} — ${r.description}</div></div>
      <div><label>Business operation</label><div><code>${r.business_operation_id}</code></div></div>
      <div><label>Operation state</label><div>${badge(op.state)}</div></div>
      <div><label>Authority</label><div>${badge(op.authority_state)}</div></div>
      <div><label>In-flight policy</label><div><code>${op.in_flight_policy}</code></div></div>
      <div><label>Receiver reference</label><div>${op.receiver_refund_reference ? `<code>${op.receiver_refund_reference}</code>` : '—'}</div></div>
      <div><label>Terminal classification</label><div>${badge(v.terminal_classification)}</div></div>
      <div><label>Verifier status</label><div>${badge(v.verification_status)}</div></div>
      <div><label>Retry eligible</label><div>${badge(v.retry_eligible ? "ALLOW" : "DENY")}</div></div>
      <div><label>Reconciliation required</label><div>${badge(v.reconciliation_required ? "RECONCILIATION_REQUIRED" : "—")}</div></div>
      <div><label>Chain valid</label><div>${badge(v.chain_valid ? "VERIFIED" : "FAILED")}</div></div>
      <div><label>Events examined</label><div>${v.evidence_events_examined}</div></div>
    </div>
    <div class="col">
      <strong>Failed assertions</strong>
      ${v.failed_assertions.length ? `<ul>${v.failed_assertions.map(x => `<li class="bad">${x}</li>`).join("")}</ul>` : '<div class="muted">none</div>'}
      <strong>Unresolved conditions</strong>
      ${v.unresolved_conditions.length ? `<ul>${v.unresolved_conditions.map(x => `<li class="warn">${x}</li>`).join("")}</ul>` : '<div class="muted">none</div>'}
      <strong>Notes</strong>
      ${r.notes.length ? `<ul>${r.notes.map(x => `<li>${x}</li>`).join("")}</ul>` : '<div class="muted">none</div>'}
    </div>
  `;
}

function renderTimeline(ev) {
  const tbody = document.querySelector("#timeline tbody");
  tbody.innerHTML = "";
  for (const e of ev.events) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${e.sequence}</td>
      <td>${badge(e.event_type)}</td>
      <td>${e.previous_state || "—"} → ${e.new_state || "—"}</td>
      <td><small>${e.timestamp}</small></td>
      <td><pre>${JSON.stringify(e.payload || {}, null, 2)}</pre></td>
    `;
    tbody.appendChild(tr);
  }
}

document.getElementById("btn-verify-ledger").onclick = async () => {
  const el = document.getElementById("ledger-status");
  try {
    const r = await api("/evidence/ledger/verify");
    el.innerHTML = badge(r.chain_valid ? "VERIFIED" : "FAILED") + ` ${r.event_count} events`;
  } catch (e) {
    el.textContent = e.message;
  }
};

loadScenarios().catch(e => document.getElementById("scenarios").textContent = e.message);