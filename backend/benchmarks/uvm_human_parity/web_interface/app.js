"use strict";

const DEFAULT_ROLES = [
  "package",
  "interface",
  "seq_item",
  "sequencer",
  "driver",
  "monitor",
  "agent",
  "env",
  "scoreboard",
  "coverage",
  "tests",
  "top",
  "filelist"
];

const SCORE_WEIGHTS = {
  file_completeness: 15,
  interface_fidelity: 15,
  uvm_architecture: 15,
  driver_monitor_quality: 15,
  scoreboard_quality: 15,
  coverage_intent: 10,
  sequence_quality: 10,
  maintainability: 5
};

const state = {
  humanFiles: [],
  generatedFiles: [],
  rtlFiles: [],
  functionalFile: null,
  result: null
};

const el = (id) => document.getElementById(id);

function init() {
  renderRoleChecks();
  bindInputs();
  bindTabs();
  bindActions();
}

function renderRoleChecks() {
  const box = el("roleChecks");
  box.innerHTML = "";
  DEFAULT_ROLES.forEach((role) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = role;
    input.checked = true;
    label.append(input, document.createTextNode(role));
    box.appendChild(label);
  });
}

function bindInputs() {
  el("humanInput").addEventListener("change", (event) => {
    state.humanFiles = Array.from(event.target.files || []);
    el("humanCount").textContent = `${state.humanFiles.length} files`;
  });
  el("generatedInput").addEventListener("change", (event) => {
    state.generatedFiles = Array.from(event.target.files || []);
    el("generatedCount").textContent = `${state.generatedFiles.length} files`;
  });
  el("rtlInput").addEventListener("change", (event) => {
    state.rtlFiles = Array.from(event.target.files || []);
    el("rtlCount").textContent = `${state.rtlFiles.length} files`;
  });
  el("functionalInput").addEventListener("change", (event) => {
    state.functionalFile = Array.from(event.target.files || [])[0] || null;
    el("functionalCount").textContent = state.functionalFile ? "1 file" : "0 files";
  });
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      const tab = button.dataset.tab;
      ["issues", "roles", "plan", "json"].forEach((name) => {
        el(`${name}Panel`).classList.toggle("hidden", name !== tab);
      });
    });
  });
}

function bindActions() {
  el("runButton").addEventListener("click", runBenchmark);
  el("clearButton").addEventListener("click", clearResult);
  el("resetRoles").addEventListener("click", renderRoleChecks);
  el("downloadScorecard").addEventListener("click", () => downloadJson("scorecard.json", scorecardPayload(state.result)));
  el("downloadIssues").addEventListener("click", () => downloadJson("issues.json", state.result.issues));
  el("downloadReport").addEventListener("click", () => downloadText("comparison_report.md", renderMarkdownReport(state.result)));
}

async function runBenchmark() {
  const caseId = el("caseId").value.trim() || "custom_uvm_case";
  const topModule = el("topModule").value.trim() || "dut";
  const requiredRoles = Array.from(document.querySelectorAll("#roleChecks input:checked")).map((item) => item.value);

  const human = await readFileSet(state.humanFiles);
  const generated = await readFileSet(state.generatedFiles);
  const rtlTexts = await Promise.all(state.rtlFiles.map(async (file) => ({ name: file.name, text: await file.text() })));
  const functionalText = state.functionalFile ? await state.functionalFile.text() : "";

  const result = evaluate({
    caseId,
    topModule,
    requiredRoles,
    human,
    generated,
    rtlTexts,
    functionalText
  });
  state.result = result;
  renderResult(result);
}

async function readFileSet(files) {
  const allowed = [".sv", ".svh", ".v", ".vh", ".f", ".flist"];
  const result = [];
  for (const file of files) {
    const name = file.webkitRelativePath || file.name;
    if (!allowed.some((suffix) => name.toLowerCase().endsWith(suffix))) {
      continue;
    }
    result.push({ name, text: await file.text() });
  }
  return result;
}

function evaluate(input) {
  const issues = [];
  const humanRoles = discoverRoles(input.human);
  const generatedRoles = discoverRoles(input.generated);
  const expectedPorts = expectedPortsFromRtl(input.rtlTexts, input.topModule);
  const humanInterfaceSignals = signalsForRole(humanRoles, "interface");
  const generatedInterfaceSignals = signalsForRole(generatedRoles, "interface");

  if (input.human.length === 0) {
    addIssue(issues, "error", "human_reference_missing", "No human-authored UVM files were uploaded.");
  }
  if (input.generated.length === 0) {
    addIssue(issues, "error", "generated_reference_missing", "No generated UVM files were uploaded.");
  }

  input.requiredRoles.forEach((role) => {
    if (!generatedRoles[role]) {
      addIssue(issues, "error", "missing_required_role", `Generated UVM is missing required role '${role}'.`, "", role);
    }
    if (humanRoles[role] && !generatedRoles[role]) {
      addIssue(issues, "error", "missing_human_reference_role", `Human reference includes role '${role}', but generated UVM does not.`, "", role);
    }
  });

  const expected = expectedPorts.size ? expectedPorts : humanInterfaceSignals;
  if (generatedInterfaceSignals.size) {
    expected.forEach((port) => {
      if (!generatedInterfaceSignals.has(port)) {
        addIssue(issues, "error", "missing_interface_signal", `Generated interface is missing DUT signal '${port}'.`, "", "interface");
      }
    });
    generatedInterfaceSignals.forEach((signal) => {
      if (!expected.has(signal) && !signal.startsWith("m_") && !signal.startsWith("cfg_")) {
        addIssue(issues, "error", "hallucinated_signal", `Generated interface declares non-DUT signal '${signal}'.`, "", "interface");
      }
    });
  }

  input.generated.forEach((file) => {
    if (/\bTODO\b|\bFIXME\b|placeholder|not implemented/i.test(file.text)) {
      addIssue(issues, "warning", "placeholder_logic", "Generated source contains TODO/FIXME/placeholder text.", file.name);
    }
    if (/```/.test(file.text)) {
      addIssue(issues, "error", "generated_file_invalid", "Generated source still contains markdown code fences.", file.name);
    }
  });

  checkScoreboard(generatedRoles, issues);
  checkCoverage(generatedRoles, input.functionalText, issues);
  checkDriverMonitor(generatedRoles, issues);
  checkArchitecture(input.generated, generatedRoles, issues);

  const categories = scoreCategories(input.requiredRoles, generatedRoles, issues, input.generated);
  const total = Object.values(categories).reduce((sum, value) => sum + value, 0);
  return {
    case_id: input.caseId,
    top_module: input.topModule,
    human_parity_score: total,
    categories,
    hard_flags: Array.from(new Set(issues.filter((issue) => issue.severity === "error").map((issue) => issue.code))).sort(),
    issues,
    human_roles: rolesToNames(humanRoles),
    generated_roles: rolesToNames(generatedRoles)
  };
}

function discoverRoles(files) {
  const roles = {};
  files.forEach((file) => {
    rolesForFile(file.name.toLowerCase(), file.text).forEach((role) => {
      roles[role] ||= [];
      roles[role].push(file);
    });
  });
  return roles;
}

function rolesForFile(name, text) {
  const roles = new Set();
  const stripped = stripCommentsAndStrings(text);
  if (name.endsWith(".f") || name.endsWith(".flist")) roles.add("filelist");
  if (name.endsWith("_pkg.sv") || /\bpackage\s+[a-zA-Z_]\w*/.test(stripped)) roles.add("package");
  if (name.endsWith("_if.sv") || name.endsWith("_intf.sv") || /\binterface\s+[a-zA-Z_]\w*/.test(stripped)) roles.add("interface");
  if (name.includes("seq_item") || name.includes("sequence_item") || stripped.includes("extends uvm_sequence_item")) roles.add("seq_item");
  if (name.includes("sequencer") || stripped.includes("extends uvm_sequencer")) roles.add("sequencer");
  if (name.includes("driver") || stripped.includes("extends uvm_driver")) roles.add("driver");
  if (name.includes("monitor") || stripped.includes("extends uvm_monitor")) roles.add("monitor");
  if (name.endsWith("_agent.sv") || stripped.includes("extends uvm_agent")) roles.add("agent");
  if (name.endsWith("_env.sv") || stripped.includes("extends uvm_env")) roles.add("env");
  if (name.includes("scoreboard") || stripped.includes("extends uvm_scoreboard")) roles.add("scoreboard");
  if (name.includes("coverage") || /\bcovergroup\b|\bcoverpoint\b/.test(stripped)) roles.add("coverage");
  if (name.endsWith("_test.sv") || name.endsWith("_tests.sv") || stripped.includes("extends uvm_test")) roles.add("tests");
  if (name.endsWith("top_tb.sv") || name.endsWith("testbench.sv") || stripped.includes("run_test")) roles.add("top");
  if (name.includes("seq_lib") || stripped.includes("extends uvm_sequence")) roles.add("sequence_library");
  return roles;
}

function expectedPortsFromRtl(files, topModule) {
  const ports = new Set();
  files.forEach((file) => {
    const text = stripSvComments(file.text);
    const re = new RegExp(`\\bmodule\\s+${escapeRegExp(topModule)}\\b([\\s\\S]*?)\\bendmodule\\b`);
    const match = text.match(re);
    const source = match ? match[1] : text;
    source.split(/\r?\n/).forEach((raw) => {
      if (!/\b(input|output|inout)\b/.test(raw)) return;
      let line = raw.replace(/\b(input|output|inout|wire|logic|reg|bit|signed|unsigned)\b/g, " ");
      line = line.replace(/\[[^\]]+\]/g, " ").replace(/[);]/g, " ");
      line.split(",").forEach((part) => {
        const tokens = part.match(/\b[a-zA-Z_]\w*\b/g);
        if (tokens && tokens.length) ports.add(tokens[tokens.length - 1]);
      });
    });
  });
  return ports;
}

function signalsForRole(roles, role) {
  const signals = new Set();
  (roles[role] || []).forEach((file) => {
    extractDeclaredSignals(file.text).forEach((signal) => signals.add(signal));
  });
  return signals;
}

function extractDeclaredSignals(text) {
  const signals = new Set();
  stripSvComments(text).split(/\r?\n/).forEach((raw) => {
    if (!/\b(input|output|inout|logic|wire|reg|bit)\b/.test(raw)) return;
    if (/\b(class|module|interface|package|function|task)\b/.test(raw)) return;
    let line = raw.replace(/\b(input|output|inout|logic|wire|reg|bit|signed|unsigned)\b/g, " ");
    line = line.replace(/\[[^\]]+\]/g, " ").replace(/[);]/g, " ");
    line.split(",").forEach((part) => {
      const tokens = part.match(/\b[a-zA-Z_]\w*\b/g);
      if (tokens && tokens.length) signals.add(tokens[tokens.length - 1]);
    });
  });
  return signals;
}

function checkScoreboard(roles, issues) {
  (roles.scoreboard || []).forEach((file) => {
    const text = file.text.toLowerCase();
    const hasFailure = ["`uvm_error", "uvm_error", "$error", "uvm_fatal", "fail_count"].some((token) => text.includes(token));
    const hasCompare = /!==|!=|assert\s*\(|compare|expected|predict|reference/.test(text);
    if (!hasFailure || !hasCompare) {
      addIssue(issues, "error", "weak_scoreboard", "Scoreboard does not show a real compare plus failure path.", file.name, "scoreboard");
    }
  });
}

function checkCoverage(roles, functionalText, issues) {
  const files = roles.coverage || [];
  if (!files.length) {
    addIssue(issues, "error", "missing_covergroup", "Generated UVM has no coverage role.", "", "coverage");
    return;
  }
  const text = files.map((file) => file.text).join("\n");
  if (!/\bcovergroup\b/.test(text)) {
    addIssue(issues, "error", "missing_covergroup", "Coverage files do not define a covergroup.", "", "coverage");
  }
  const tokens = functionalTokens(functionalText);
  if (tokens.length && !tokens.some((token) => text.toLowerCase().includes(token.toLowerCase()))) {
    addIssue(issues, "warning", "coverage_not_spec_grounded", "Coverage does not reference any functional point ids/names.", "", "coverage");
  }
}

function functionalTokens(text) {
  if (!text.trim()) return [];
  try {
    const data = JSON.parse(text);
    const tokens = [];
    const visit = (value) => {
      if (Array.isArray(value)) {
        value.forEach(visit);
      } else if (value && typeof value === "object") {
        ["id", "name", "signal", "point"].forEach((key) => {
          if (typeof value[key] === "string" && value[key].trim()) tokens.push(value[key].trim());
        });
        Object.values(value).forEach(visit);
      }
    };
    visit(data);
    return tokens;
  } catch {
    return [];
  }
}

function checkDriverMonitor(roles, issues) {
  ["driver", "monitor"].forEach((role) => {
    (roles[role] || []).forEach((file) => {
      const text = file.text.toLowerCase();
      const hasTiming = text.includes("@(posedge") || text.includes("@(");
      const hasVif = text.includes("vif") || text.includes("virtual");
      const hasReset = text.includes("reset") || text.includes("rst");
      const hasActivity = role === "driver"
        ? text.includes("seq_item_port") || text.includes("get_next_item")
        : text.includes("analysis_port") && text.includes(".write");
      if (!(hasTiming && hasVif && hasReset && hasActivity)) {
        addIssue(issues, "warning", "driver_monitor_missing_protocol_activity", `Generated ${role} lacks timing, reset, virtual interface, or transaction activity.`, file.name, role);
      }
    });
  });
}

function checkArchitecture(files, roles, issues) {
  const text = files.map((file) => file.text).join("\n");
  if (files.length && !text.includes("`uvm_component_utils") && !text.includes("`uvm_object_utils")) {
    addIssue(issues, "warning", "missing_uvm_factory_macros", "Generated files do not use UVM factory macros.");
  }
  if (roles.agent && !text.includes("connect_phase")) {
    addIssue(issues, "warning", "missing_connect_phase", "Generated UVM does not define a visible connect_phase.");
  }
  if (roles.top && !text.includes("run_test")) {
    addIssue(issues, "error", "top_missing_run_test", "Top-level UVM file does not call run_test().", "", "top");
  }
}

function scoreCategories(requiredRoles, generatedRoles, issues, generatedFiles) {
  if (!generatedFiles.length) {
    return Object.fromEntries(Object.keys(SCORE_WEIGHTS).map((key) => [key, 0]));
  }
  const present = requiredRoles.filter((role) => generatedRoles[role]).length;
  return {
    file_completeness: clamp(Math.round(SCORE_WEIGHTS.file_completeness * present / Math.max(1, requiredRoles.length)), 0, 15),
    interface_fidelity: afterPenalty("interface_fidelity", issueCount(issues, ["hallucinated_signal", "missing_interface_signal"]) * 3),
    uvm_architecture: afterPenalty("uvm_architecture", issueCount(issues, ["missing_uvm_factory_macros", "missing_connect_phase", "top_missing_run_test"]) * 4),
    driver_monitor_quality: afterPenalty("driver_monitor_quality", issueCount(issues, ["driver_monitor_missing_protocol_activity"]) * 4),
    scoreboard_quality: afterPenalty("scoreboard_quality", generatedRoles.scoreboard ? issueCount(issues, ["weak_scoreboard"]) * 8 : 15),
    coverage_intent: afterPenalty("coverage_intent", generatedRoles.coverage ? issueCount(issues, ["missing_covergroup"]) * 8 + issueCount(issues, ["coverage_not_spec_grounded"]) * 3 : 10),
    sequence_quality: afterPenalty("sequence_quality", generatedRoles.sequence_library || generatedRoles.tests ? 0 : 7),
    maintainability: afterPenalty("maintainability", issueCount(issues, ["placeholder_logic", "generated_file_invalid"]) * 2)
  };
}

function afterPenalty(category, penalty) {
  return clamp(SCORE_WEIGHTS[category] - penalty, 0, SCORE_WEIGHTS[category]);
}

function issueCount(issues, codes) {
  return issues.filter((issue) => codes.includes(issue.code)).length;
}

function addIssue(issues, severity, code, message, file = "", role = "") {
  issues.push({ severity, code, message, file, role });
}

function rolesToNames(roles) {
  const result = {};
  Object.entries(roles).forEach(([role, files]) => {
    result[role] = files.map((file) => file.name);
  });
  return result;
}

function renderResult(result) {
  el("statusPill").textContent = "Benchmark complete";
  el("resultSubtitle").textContent = `${result.case_id} on ${result.top_module}`;
  const score = el("scoreCircle");
  score.textContent = result.human_parity_score;
  score.className = "score-circle";
  score.classList.add(result.human_parity_score >= 85 ? "good" : result.human_parity_score >= 60 ? "warn" : "bad");

  el("metrics").innerHTML = Object.entries(result.categories).map(([name, value]) => (
    `<div class="metric"><span>${escapeHtml(name.replaceAll("_", " "))}</span><strong>${value}</strong></div>`
  )).join("");

  renderIssues(result);
  renderRoles(result);
  el("planPanel").innerHTML = renderImprovementPlan(result);
  el("jsonPanel").textContent = JSON.stringify(result, null, 2);
  ["downloadScorecard", "downloadIssues", "downloadReport"].forEach((id) => {
    el(id).disabled = false;
  });
}

function renderIssues(result) {
  const panel = el("issuesPanel");
  if (!result.issues.length) {
    panel.innerHTML = "<p>No static parity issues were detected.</p>";
    return;
  }
  panel.innerHTML = result.issues.map((issue) => (
    `<div class="issue ${escapeHtml(issue.severity)}">
      <div class="code">${escapeHtml(issue.severity.toUpperCase())} ${escapeHtml(issue.code)}${issue.role ? ` / ${escapeHtml(issue.role)}` : ""}</div>
      <div>${escapeHtml(issue.message)}</div>
      ${issue.file ? `<small>${escapeHtml(issue.file)}</small>` : ""}
    </div>`
  )).join("");
}

function renderRoles(result) {
  const roles = Array.from(new Set([...Object.keys(result.human_roles), ...Object.keys(result.generated_roles), ...DEFAULT_ROLES])).sort();
  el("rolesPanel").innerHTML = `
    <table class="role-table">
      <thead><tr><th>Role</th><th>Human</th><th>Generated</th></tr></thead>
      <tbody>
        ${roles.map((role) => `
          <tr>
            <td><strong>${escapeHtml(role)}</strong></td>
            <td>${fileList(result.human_roles[role])}</td>
            <td>${fileList(result.generated_roles[role])}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function fileList(files) {
  if (!files || !files.length) return "<span class=\"muted\">missing</span>";
  return files.map((name) => `<div>${escapeHtml(name)}</div>`).join("");
}

function renderImprovementPlan(result) {
  const counts = {};
  result.issues.forEach((issue) => {
    counts[issue.code] = (counts[issue.code] || 0) + 1;
  });
  const codes = Object.keys(counts);
  if (!codes.length) return "<p>No static gaps detected. Next step: simulator-backed validation.</p>";
  return `
    <ul>
      ${codes.map((code) => `<li><strong>${escapeHtml(code)}</strong> (${counts[code]}): ${escapeHtml(recommendationFor(code))}</li>`).join("")}
    </ul>
  `;
}

function recommendationFor(code) {
  const recommendations = {
    generated_reference_missing: "Upload the generated UVM directory or run ChipVerify generation first.",
    missing_required_role: "Update generation to emit the complete required UVM component set.",
    hallucinated_signal: "Ground interface generation on parsed DUT ports and reject undeclared signals.",
    missing_interface_signal: "Preserve every DUT port in interface, top, driver, and monitor.",
    weak_scoreboard: "Generate real expected-vs-actual comparisons with UVM error paths.",
    missing_covergroup: "Emit covergroups tied to functional points.",
    coverage_not_spec_grounded: "Name/comment coverpoints with functional point IDs.",
    driver_monitor_missing_protocol_activity: "Strengthen driver/monitor generation for reset, clocking, VIF, and TLM activity.",
    placeholder_logic: "Reject TODO/placeholder source before reporting completion."
  };
  return recommendations[code] || "Inspect generated files and update the generator contract for this finding.";
}

function clearResult() {
  state.result = null;
  el("statusPill").textContent = "Waiting for files";
  el("resultSubtitle").textContent = "Upload human and generated UVM files to start.";
  el("scoreCircle").textContent = "--";
  el("scoreCircle").className = "score-circle";
  el("metrics").innerHTML = "";
  el("issuesPanel").innerHTML = "";
  el("rolesPanel").innerHTML = "";
  el("planPanel").innerHTML = "";
  el("jsonPanel").textContent = "";
  ["downloadScorecard", "downloadIssues", "downloadReport"].forEach((id) => {
    el(id).disabled = true;
  });
}

function scorecardPayload(result) {
  return {
    case_id: result.case_id,
    top_module: result.top_module,
    human_parity_score: result.human_parity_score,
    categories: result.categories,
    hard_flags: result.hard_flags
  };
}

function renderMarkdownReport(result) {
  const lines = [
    `# UVM Human-Parity Report: ${result.case_id}`,
    "",
    `- Top module: \`${result.top_module}\``,
    `- Human-parity score: **${result.human_parity_score}/100**`,
    "",
    "## Category Scores",
    ""
  ];
  Object.entries(result.categories).forEach(([name, value]) => {
    lines.push(`- \`${name}\`: ${value}/${SCORE_WEIGHTS[name]}`);
  });
  lines.push("", "## Issues", "");
  if (!result.issues.length) {
    lines.push("No static parity issues were detected.");
  } else {
    result.issues.forEach((issue) => {
      lines.push(`- **${issue.severity.toUpperCase()}** \`${issue.code}\`: ${issue.message}${issue.file ? ` (${issue.file})` : ""}`);
    });
  }
  return lines.join("\n") + "\n";
}

function downloadJson(filename, payload) {
  downloadText(filename, JSON.stringify(payload, null, 2));
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function stripSvComments(text) {
  return (text || "").replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/.*/g, " ");
}

function stripCommentsAndStrings(text) {
  return stripSvComments(text).replace(/"(?:\\.|[^"\\])*"/g, "\"\"");
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

document.addEventListener("DOMContentLoaded", init);
