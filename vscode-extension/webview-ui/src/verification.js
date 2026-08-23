(function () {
  const vscode = acquireVsCodeApi();
  let state = { status: "idle", prepared: null, plan: null, events: [], result: null, patches: [], error: "" };
  const $ = (id) => document.getElementById(id);

  function render() {
    const plan = state.plan?.plan || state.plan || null;
    const uvm = plan?.uvm || {};
    const hasPlan = Boolean(plan);
    const hasResult = Boolean(state.result);
    $("app").innerHTML = `
      <div class="hero compact">
        <div class="kicker">Verification</div>
        <h1>${hasResult ? "Verification result" : "Staged verification"}</h1>
        <p>${escapeHtml(state.error || summaryText(plan))}</p>
        <div class="stepper">
          ${step("Prepare", state.prepared || hasPlan || hasResult)}
          ${step("Plan", hasPlan || hasResult)}
          ${step("Approve", hasPlan || hasResult, hasPlan && !hasResult)}
          ${step("Run", hasResult, !hasResult && hasPlan)}
          ${step("Verdict", hasResult, hasResult)}
        </div>
        <div class="row">
          <button id="prepare">Prepare</button>
          <button id="plan" class="secondary">Plan</button>
          <button id="execute" class="secondary">Execute</button>
          <button id="log" class="secondary">Upload log</button>
        </div>
      </div>
      <div class="grid">
        <div class="metric"><div class="label">Status</div><div class="value">${escapeHtml(state.status)}</div></div>
        <div class="metric"><div class="label">Agents</div><div class="value">${(uvm.agents || []).length || "-"}</div></div>
        <div class="metric"><div class="label">Sequences</div><div class="value">${(uvm.sequences || []).length || "-"}</div></div>
      </div>
      ${hasPlan ? planCard(plan) : emptyPlan()}
      ${hasResult ? resultCard(state.result) : ""}
      <div class="card stack">
        <h3>Technical log</h3>
        <pre>${escapeHtml(state.events.slice(-60).map((e) => `${e.phase || e.type || "event"}: ${e.message || e.file || ""}`).join("\n") || "No run events yet.")}</pre>
      </div>
    `;
    $("prepare").onclick = () => vscode.postMessage({ type: "prepare" });
    $("plan").onclick = () => vscode.postMessage({ type: "plan" });
    $("execute").onclick = () => vscode.postMessage({ type: "execute" });
    $("log").onclick = () => vscode.postMessage({ type: "uploadSimulatorLog" });
  }

  function summaryText(plan) {
    if (!plan) return "Build TruthCore first, then prepare a plan and generate verification collateral.";
    const summary = plan.presentation?.summary || plan.summary || plan.description;
    return summary || "Review the generated strategy before execution.";
  }

  function step(label, done, active = false) {
    return `<div class="step ${done ? "done" : ""} ${active ? "active" : ""}">${escapeHtml(label)}</div>`;
  }

  function emptyPlan() {
    return `
      <div class="empty">
        <h3>No plan yet</h3>
        <p>Use the flow in order: attach files, build TruthCore, prepare, then plan. Generated UVM, Formal, and UnitSim artifacts will sync into <b>chipix_out</b>.</p>
      </div>
    `;
  }

  function planCard(plan) {
    const uvm = plan.uvm || {};
    const formal = plan.formal || {};
    const unitsim = plan.unitsim || {};
    const steps = [];
    if (Object.keys(uvm).length) steps.push(`UVM - ${(uvm.agents || []).length} agents, ${(uvm.sequences || []).length} sequences, ${(uvm.coverage_points || []).length} coverage points`);
    if (Object.keys(formal).length) steps.push(`Formal - ${(formal.assertions || []).length} assertions, ${(formal.covers || []).length} covers`);
    if (Object.keys(unitsim).length) steps.push(`UnitSim - ${(unitsim.scenarios || []).length} scenarios`);
    return `<div class="card stack"><h2>Plan ready</h2><p>${escapeHtml(summaryText(plan))}</p><div class="list">${steps.map((s) => `<div class="item">${escapeHtml(s)}</div>`).join("") || `<div class="item muted">Plan payload available; no tactical steps found.</div>`}</div></div>`;
  }

  function resultCard(result) {
    const ok = /pass|complete|validated/i.test(String(result.status || result.summary || ""));
    return `<div class="card stack"><h2 class="${ok ? "good" : "bad"}">${ok ? "Verification complete" : "Verification needs attention"}</h2><p>${escapeHtml(result.summary || result.status || "Run finished.")}</p></div>`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (m) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[m]));
  }

  window.addEventListener("message", (event) => {
    if (event.data?.type === "state") {
      state = { ...state, ...event.data.state };
      render();
    }
  });
  render();
  vscode.postMessage({ type: "ready" });
})();
