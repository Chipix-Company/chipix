(function () {
  const vscode = acquireVsCodeApi();
  let state = { loading: true, model: null, project: null, selected: null, aspect: "overview", error: "" };

  const $ = (id) => document.getElementById(id);

  function normalize(raw) {
    if (!raw) return null;
    const revision = raw.mental_model && typeof raw.mental_model === "object" ? raw.mental_model : raw;
    const content = revision.content && typeof revision.content === "object" ? revision.content : raw.content || revision;
    const model = content.content && typeof content.content === "object" ? content.content : content;
    const design = model.design && typeof model.design === "object" ? model.design : {};
    return {
      ...design,
      ...model,
      id: revision.id || raw.id,
      revision: revision.revision || raw.revision,
      summary_text: revision.summary_text || raw.summary_text,
      top_module: model.top_module || design.top_module || "",
      modules: model.modules || design.modules || [],
      ports: model.ports || design.ports || [],
      parameters: model.parameters || design.parameters || [],
      requirements: model.requirements || design.requirements || [],
      protocols: model.protocols || design.protocols || [],
      fsms: model.fsms || design.fsms || [],
      expected_behaviors: model.expected_behaviors || design.expected_behaviors || [],
      open_questions: model.open_questions || design.open_questions || [],
      hierarchy_tree: model.hierarchy_tree || design.hierarchy_tree || {},
      block_models: model.block_models || design.block_models || {},
      description: model.description || design.description || raw.summary_text || ""
    };
  }

  function moduleName(raw, fallback = "") {
    return raw?.name || raw?.module_name || raw?.module || raw?.top_module || fallback;
  }

  function selectedBlock(model) {
    if (!model || !state.selected || state.selected === model.top_module) return model;
    const block = model.block_models?.[state.selected];
    if (block) return { ...block, name: state.selected };
    const item = (model.modules || []).find((m) => typeof m === "object" && moduleName(m) === state.selected);
    return item ? { ...item, name: state.selected } : null;
  }

  function portsFor(model, block) {
    if (!model) return [];
    if (!state.selected || state.selected === model.top_module) return model.ports || [];
    if (Array.isArray(block?.ports) && block.ports.length) return block.ports;
    return (model.ports || []).filter((p) => [p.module, p.module_name, p.parent_module, p.top_module].includes(state.selected));
  }

  function parametersFor(model, block) {
    if (!model) return [];
    if (!state.selected || state.selected === model.top_module) return model.parameters || [];
    if (Array.isArray(block?.parameters) && block.parameters.length) return block.parameters;
    return (model.parameters || []).filter((p) => [p.module, p.module_name, p.parent_module, p.top_module].includes(state.selected));
  }

  function moduleList(model) {
    if (!model) return [];
    const names = new Set();
    if (model.top_module) names.add(model.top_module);
    (model.modules || []).forEach((m) => names.add(typeof m === "string" ? m : moduleName(m)));
    Object.keys(model.hierarchy_tree || {}).forEach((m) => names.add(m));
    Object.values(model.hierarchy_tree || {}).flat().forEach((m) => names.add(m));
    return Array.from(names).filter(Boolean);
  }

  function narrative(model, block, ports) {
    if (!model) return "Build TruthCore from your active spec and RTL to see how Chipix understands the design.";
    if (state.selected) {
      const desc = block?.description || block?.summary || "";
      if (desc) return desc;
      const children = model.hierarchy_tree?.[state.selected] || [];
      return `${state.selected} is ${state.selected === model.top_module ? "the top module" : "a submodule"} with ${ports.length} visible boundary port(s).${children.length ? ` It contains ${children.join(", ")}.` : ""}`;
    }
    return model.description || model.summary_text || `TruthCore indexed ${moduleList(model).length} module(s), ${(model.ports || []).length} port(s), and ${(model.requirements || []).length} requirement(s).`;
  }

  function renderGraph(model) {
    const graph = $("graph");
    graph.innerHTML = "";
    const modules = moduleList(model).slice(0, 12);
    if (!modules.length) {
      graph.innerHTML = `<div class="muted" style="padding:16px">No hierarchy yet.</div>`;
      return;
    }
    const top = model.top_module || modules[0];
    const centerX = 29;
    const topNode = makeNode(top, centerX, 24, top);
    graph.appendChild(topNode);
    const children = modules.filter((m) => m !== top);
    children.forEach((name, i) => {
      const x = 5 + (i % 2) * 48;
      const y = 120 + Math.floor(i / 2) * 56;
      graph.appendChild(makeEdge(centerX + 10, 76, x + 14, y));
      graph.appendChild(makeNode(name, x, y, name));
    });
  }

  function makeNode(label, xPct, y, value) {
    const el = document.createElement("button");
    el.className = `node ${state.selected === value ? "selected" : ""}`;
    el.style.left = `${xPct}%`;
    el.style.top = `${y}px`;
    el.textContent = label;
    el.onclick = () => {
      state.selected = state.selected === value ? null : value;
      render();
    };
    return el;
  }

  function makeEdge(x1, y1, x2, y2) {
    const el = document.createElement("div");
    const graphWidth = Math.max(1, $("graph").clientWidth);
    const ax = graphWidth * x1 / 100;
    const bx = graphWidth * x2 / 100;
    const dx = bx - ax;
    const dy = y2 - y1;
    el.className = "edge";
    el.style.left = `${ax}px`;
    el.style.top = `${y1}px`;
    el.style.width = `${Math.sqrt(dx * dx + dy * dy)}px`;
    el.style.transform = `rotate(${Math.atan2(dy, dx)}rad)`;
    return el;
  }

  function renderDetail(model, block, ports, parameters) {
    const detail = $("detail");
    if (!model) {
      detail.innerHTML = `<div class="card"><h3>No TruthCore model</h3><p>Attach spec/RTL and build TruthCore.</p></div>`;
      return;
    }
    const aspect = state.aspect;
    const rows = {
      overview: [
        ["Top module", model.top_module || "-"],
        ["Modules", moduleList(model).length],
        ["Ports", ports.length],
        ["Parameters", parameters.length],
        ["Requirements", (model.requirements || []).length]
      ],
      modules: moduleList(model).map((m) => ["Module", m]),
      ports: ports.map((p) => [p.direction || p.dir || "port", `${p.name || p.port || "-"} ${p.width ? `[${p.width}]` : ""}`]),
      parameters: parameters.map((p) => [p.name || "parameter", p.value ?? p.default ?? "-"]),
      behavior: [
        ...(model.protocols || []).map((p) => ["Protocol", p.name || p.description || JSON.stringify(p)]),
        ...(model.fsms || []).map((f) => ["FSM", f.name || f.state_signal || JSON.stringify(f)]),
        ...(model.expected_behaviors || []).map((b) => ["Behavior", b.description || b.name || JSON.stringify(b)])
      ],
      questions: (model.open_questions || []).map((q) => [q.blocking ? "Blocking" : "Question", q.question || q.text || JSON.stringify(q)])
    }[aspect] || [];
    detail.innerHTML = rows.length
      ? `<div class="list">${rows.map(([a,b]) => `<div class="item"><b>${escapeHtml(a)}</b><div class="muted">${escapeHtml(String(b))}</div></div>`).join("")}</div>`
      : `<div class="card muted">No ${aspect} data recorded for this scope.</div>`;
  }

  function render() {
    const model = normalize(state.model);
    const block = selectedBlock(model);
    const ports = portsFor(model, block);
    const parameters = parametersFor(model, block);
    const emptyHint = state.project
      ? "Attach RTL and optionally a spec, then build TruthCore."
      : "Open a workspace folder, attach RTL/spec, then build TruthCore.";
    $("app").innerHTML = `
      <div class="hero compact">
        <div class="kicker">TruthCore</div>
        <h1>${escapeHtml(state.selected || model?.top_module || "No model yet")}</h1>
        <p>${escapeHtml(state.error || (model ? narrative(model, block, ports) : emptyHint))}</p>
        <div class="status-strip">
          <span class="dot ${state.error ? "bad" : model ? "good" : "warn"}"></span>
          <span>${escapeHtml(state.error ? "Action needed" : model ? "Model ready" : "Waiting for project files")}</span>
        </div>
        <div class="row">
          <button id="build">Build TruthCore</button>
          <button id="refresh" class="secondary">Refresh</button>
          <button id="attachSpec" class="secondary">Spec</button>
          <button id="attachRtl" class="secondary">RTL</button>
        </div>
      </div>
      <div class="grid">
        <div class="metric"><div class="label">Modules</div><div class="value">${model ? moduleList(model).length : "-"}</div></div>
        <div class="metric"><div class="label">Ports</div><div class="value">${model ? (model.ports || []).length : "-"}</div></div>
        <div class="metric"><div class="label">Requirements</div><div class="value">${model ? (model.requirements || []).length : "-"}</div></div>
      </div>
      ${model ? `<div id="graph" class="graph"></div>` : emptyTruthCore()}
      <div class="tabs">
        ${["overview","modules","ports","parameters","behavior","questions"].map((id) => `<button class="tab ${state.aspect === id ? "active" : ""}" data-aspect="${id}">${id}</button>`).join("")}
      </div>
      <div id="detail"></div>
    `;
    $("build").onclick = () => vscode.postMessage({ type: "buildTruthCore" });
    $("refresh").onclick = () => vscode.postMessage({ type: "refresh" });
    $("attachSpec").onclick = () => vscode.postMessage({ type: "selectSpec" });
    $("attachRtl").onclick = () => vscode.postMessage({ type: "attachRtlWorkspace" });
    document.querySelectorAll("[data-aspect]").forEach((btn) => {
      btn.onclick = () => {
        state.aspect = btn.dataset.aspect;
        render();
      };
    });
    if (model) renderGraph(model);
    renderDetail(model, block, ports, parameters);
  }

  function emptyTruthCore() {
    return `
      <div id="graph" class="empty">
        <h3>Start with your workspace</h3>
        <p>Attach the spec and RTL from this VS Code folder. Chipix will create a backend project, index the design, and display hierarchy, ports, requirements, protocols, and open questions here.</p>
      </div>
    `;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (m) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[m]));
  }

  window.addEventListener("message", (event) => {
    if (event.data?.type === "state") {
      state = { ...state, ...event.data.state, loading: false };
      render();
    }
  });

  render();
  vscode.postMessage({ type: "ready" });
})();
