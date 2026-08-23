/**
 * useStagedVerification — thread-first hook for the staged verification flow.
 *
 * State machine (richer than the legacy stage labels):
 *   idle → preparing → recommended → planned → refining
 *        → executing → completed | failed
 *
 * The hook owns no JSX. Every transition emits one or more thread items via
 * the parent-supplied `pushItem` (and mutates them via `replaceItem` for
 * supersession / progress updates).
 *
 * Persistence
 * -----------
 * Snapshots are persisted per project to `localStorage` under the legacy
 * prefix `chipverify.stagedVerification.v1` so users with existing state
 * migrate cleanly. The snapshot JSON shape is a SUPERSET of the legacy
 * `StagedVerification.jsx` shape:
 *   {
 *     // legacy fields (still read & written for backward compat):
 *     currentStage, mentalModel, modelRevisionId, recommendation,
 *     selectedType, plan, refineFeedback, executionResult,
 *     executionActivity, executionStepIndex, executionEvents, streamedFiles,
 *     updatedAt,
 *
 *     // thread-first additions (ignored by the legacy panel):
 *     stage,            // richer state-machine label
 *     itemIds: { prep, rec, plan, run, cadence, verdict },
 *     supersededPlans,  // [string]
 *   }
 *
 * If a snapshot is found on mount with a non-`idle` resolved stage, the hook
 * pushes a tiny `Said` ("Resumed verification plan from {timestamp}") plus
 * the cards corresponding to the persisted stage so the conversation feels
 * continuous across reloads.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  prepareVerification,
  generatePlan,
  refinePlan,
  executeVerificationStream,
} from "../../api/stagedVerificationApi";
import { THREAD_KINDS, makeItem } from "./types";
import { planPayloadFromVerificationApi } from "./implementationPlan";
import { AnalyticsEvents, captureError, track } from "../../lib/observability";

const STORAGE_PREFIX = "chipverify.stagedVerification.v1";

const STRATEGY_META = {
  unitsim: { label: "Unit simulation" },
  formal: { label: "Formal verification" },
  uvm: { label: "UVM environment" },
  all: { label: "All strategies" },
};

const ALL_RATIONALE = "Run UnitSim, Formal, and UVM together. Maximum coverage for critical designs.";

function storageKey(projectId) {
  return projectId ? `${STORAGE_PREFIX}:${projectId}` : null;
}

function loadSnapshot(projectId) {
  const k = storageKey(projectId);
  if (!k || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(k);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function saveSnapshot(projectId, snapshot) {
  const k = storageKey(projectId);
  if (!k || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(k, JSON.stringify(snapshot));
  } catch {
    // localStorage failures are non-fatal — the live workflow still works.
  }
}

function clearSnapshot(projectId) {
  const k = storageKey(projectId);
  if (!k || typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(k);
  } catch {
    // ignore
  }
}

function normalizeModel(raw) {
  if (!raw) return null;
  if (raw.mental_model && raw.mental_model.content) return raw.mental_model.content;
  if (raw.content && typeof raw.content === "object" && raw.content.design) return raw.content;
  if (raw.design) return raw;
  return raw;
}

function modelSummary(model, recommendation) {
  if (!model && !recommendation) return null;
  const design = model?.design && typeof model.design === "object" ? model.design : model;
  const ports = Array.isArray(design?.ports)
    ? design.ports
    : Array.isArray(model?.ports)
      ? model.ports
      : [];
  const fsms = Array.isArray(design?.fsms)
    ? design.fsms
    : Array.isArray(model?.fsms)
      ? model.fsms
      : [];
  const parts = [];
  if (design?.top_module || model?.top_module) parts.push(`Top module: ${design?.top_module || model.top_module}`);
  if (ports.length) parts.push(`${ports.length} ports`);
  if (fsms.length) parts.push(`${fsms.length} FSMs`);
  if (recommendation?.design_complexity) parts.push(`complexity ${recommendation.design_complexity}`);
  return parts.join(" · ") || null;
}

function recommendationStrategies(recommendation) {
  const recs = Array.isArray(recommendation?.recommendations) ? recommendation.recommendations : [];
  const out = recs.map((r) => ({
    id: r.type,
    label: r.label || STRATEGY_META[r.type]?.label || r.type,
    rationale: (r.reasons || []).join(" · ") || (r.recommended ? "Good fit for this design." : "Optional for this design."),
    recommended: !!r.recommended,
    confidence: r.confidence || null,
  }));
  const recommendedCount = (recommendation?.recommended_types || []).length;
  out.push({
    id: "all",
    label: STRATEGY_META.all.label,
    rationale: ALL_RATIONALE,
    /* Never badge “Recommended” — user explicitly chooses combined run; avoids four identical greens when all three base strategies already are recommended. */
    recommended: false,
    confidence: recommendedCount >= 3 ? "medium" : "low",
  });
  return out;
}


function buildRunStages(verificationType) {
  return [
    { id: "lock", label: "Lock plan", state: "todo" },
    { id: "scaffold", label: "Scaffold", state: "todo" },
    { id: "compile", label: "Compile", state: "todo" },
    {
      id: "run",
      label: verificationType === "formal" ? "Prove" : verificationType === "uvm" ? "Cadence" : "Simulate",
      state: "todo",
    },
    { id: "verdict", label: "Verdict", state: "todo" },
  ];
}

function advanceStages(stages, event) {
  const eventType = typeof event === "string" ? event : event?.type;
  const next = stages.map((s) => ({ ...s }));
  const order = ["lock", "scaffold", "compile", "run", "verdict"];
  let target = null;
  if (eventType === "started" || eventType === "policy_selected") target = "lock";
  else if (
    eventType === "scaffold_complete"
    || eventType === "file_generated"
    || eventType === "llm_enhance_start"
    || eventType === "llm_enhance_done"
    || eventType === "llm_enhance_rejected"
    || eventType === "llm_enhance_error"
  ) target = "scaffold";
  else if (
    eventType === "compile_complete"
    || eventType === "compile"
    || eventType === "compile_gate_complete"
    || eventType === "validation_complete"
    || eventType === "cadence_detect"
    || eventType === "cadence_start"
  ) target = "compile";
  else if (
    eventType === "run_complete"
    || eventType === "run"
    || eventType === "result"
    || eventType === "cadence_complete"
    || eventType === "cadence_skipped"
  ) target = "run";
  else if (eventType === "complete" || eventType === "error") target = "verdict";
  if (!target) return next;
  const idx = order.indexOf(target);
  for (let i = 0; i <= idx; i += 1) {
    if (next[i].state !== "done") next[i].state = "done";
  }
  if (idx + 1 < next.length) next[idx + 1].state = "now";
  return next;
}

function cadenceFactValue(cadence) {
  if (!cadence || typeof cadence !== "object") return "not reported";
  const status = String(cadence.status || "unknown").toLowerCase();
  if (status !== "skipped") return status;
  const detection = String(cadence.detection?.status || "").replace(/_/g, " ");
  if (/validation|compile gate/i.test(String(cadence.reason || ""))) return "skipped · static gate";
  if (detection === "error") return "skipped · detection unavailable";
  return detection ? `skipped · ${detection}` : "skipped";
}

function strategyStatus(status) {
  const value = String(status || "idle").toLowerCase();
  if (/pass|validated|proved|complete|success/.test(value)) return "pass";
  if (/fail|error|needs_action/.test(value)) return "fail";
  if (/skip|partial|unavailable|generated|needs_review/.test(value)) return "partial";
  return value;
}

function verificationStrategiesFromResult(result) {
  const strategies = [];
  Object.entries(result?.results || {}).forEach(([key, val]) => {
    if (!val || typeof val !== "object") return;
    strategies.push({
      id: key,
      name: STRATEGY_META[key]?.label || key,
      status: strategyStatus(val.status),
      detail: val.summary || val.validation?.summary || val.error || String(val.status || ""),
    });
    if (key === "uvm" && val.cadence) {
      strategies.push({
        id: "xcelium",
        name: "Cadence Xcelium",
        status: strategyStatus(val.cadence.status),
        detail: val.cadence.reason
          || val.cadence.analysis?.summary
          || val.cadence.detection?.guidance
          || cadenceFactValue(val.cadence),
      });
    }
  });
  return strategies;
}

const CADENCE_PHASES = [
  { id: "detect", label: "Detect Xcelium", state: "todo" },
  { id: "manifest", label: "Lock regression", state: "todo" },
  { id: "integrity", label: "Sandbox integrity", state: "todo" },
  { id: "compile", label: "Compile UVM", state: "todo" },
  { id: "elaborate", label: "Elaborate top_tb", state: "todo" },
  { id: "simulate", label: "Run scenarios", state: "todo" },
  { id: "replay", label: "Replay failures", state: "todo" },
  { id: "coverage", label: "Coverage closure", state: "todo" },
  { id: "spec_audit", label: "Audit specification", state: "todo" },
  { id: "report", label: "Build bug report", state: "todo" },
];

function formatCadenceCommand(command) {
  if (!Array.isArray(command)) return String(command || "");
  return command.map((part) => {
    const value = String(part);
    return /\s/.test(value) ? `"${value.replace(/"/g, '\\"')}"` : value;
  }).join(" ");
}

function initialCadencePayload() {
  return {
    status: "detecting",
    detection: null,
    phases: CADENCE_PHASES.map((phase) => ({ ...phase })),
    events: [],
    runId: null,
    reason: "",
    summary: "",
    regression: null,
    coverage: null,
    traceability: null,
    findings: [],
    report: null,
  };
}

function cadenceResultFromExecution(result) {
  return result?.results?.uvm?.cadence || null;
}

function reduceCadencePayload(previous, event) {
  const next = {
    ...initialCadencePayload(),
    ...(previous || {}),
    phases: (previous?.phases || CADENCE_PHASES).map((phase) => ({ ...phase })),
    events: [...(previous?.events || [])],
  };
  const eventType = String(event?.type || "");
  const updatePhase = (phaseId, patch) => {
    next.phases = next.phases.map((phase) => (phase.id === phaseId ? { ...phase, ...patch } : phase));
  };
  const appendEvent = (message, extra = {}) => {
    if (!message) return;
    next.events = [...next.events, {
      ts: event?.ts || new Date().toISOString(),
      level: extra.level || (extra.passed === false ? "error" : "info"),
      message,
      command: extra.command || "",
    }].slice(-80);
  };

  if (eventType === "cadence_detect") {
    next.status = "detecting";
    updatePhase("detect", { state: "now", detail: event.message || "Checking setup and license" });
  } else if (eventType === "cadence_ready") {
    next.detection = event.detection || next.detection;
    next.status = event.detection?.run_ready ? "ready" : "detecting";
    updatePhase("detect", {
      state: event.detection?.run_ready ? "done" : "bad",
      detail: event.message || event.detection?.status || "Detection complete",
    });
  } else if (eventType === "cadence_start") {
    next.status = "running";
    updatePhase("detect", { state: "done" });
    updatePhase("compile", { state: "now", detail: "Preparing xrun compile" });
  } else if (eventType === "cadence_regression_manifest") {
    updatePhase("manifest", { state: "done", detail: event.message || "Regression locked" });
    updatePhase("integrity", { state: "now", detail: "Preparing isolated simulator sandbox" });
  } else if (eventType === "cadence_sandbox_integrity") {
    updatePhase("integrity", { state: "done", detail: event.message || "RTL hashes locked" });
    updatePhase("compile", { state: "now", detail: "Ready to compile generated UVM" });
  } else if (eventType === "cadence_phase_start") {
    const rawPhase = String(event.phase || "");
    const phaseId = rawPhase.startsWith("simulate") ? "simulate" : rawPhase.startsWith("compile") ? "compile" : rawPhase;
    next.status = "running";
    updatePhase(phaseId, {
      state: "now",
      detail: event.message || `${phaseId} started`,
      command: formatCadenceCommand(event.command),
      returncode: null,
    });
  } else if (eventType === "cadence_phase_complete") {
    const rawPhase = String(event.phase || "");
    const phaseId = rawPhase.startsWith("simulate") ? "simulate" : rawPhase.startsWith("compile") ? "compile" : rawPhase;
    updatePhase(phaseId, {
      state: event.passed ? "done" : "bad",
      detail: event.message || `${phaseId} ${event.passed ? "passed" : "failed"}`,
      returncode: event.returncode,
    });
  } else if (eventType === "cadence_replay_start") {
    updatePhase("replay", { state: "now", detail: event.message || "Replaying failure" });
  } else if (eventType === "cadence_replay_complete") {
    updatePhase("replay", { state: "done", detail: event.message || "Replay complete" });
  } else if (eventType === "cadence_coverage_start") {
    if (next.phases.find((phase) => phase.id === "replay")?.state === "todo") updatePhase("replay", { state: "skipped", detail: "No failing test required replay" });
    updatePhase("coverage", { state: "now", detail: event.message || "Reading coverage" });
  } else if (eventType === "cadence_coverage_complete") {
    next.coverage = event.coverage || next.coverage;
    updatePhase("coverage", { state: "done", detail: event.message || "Coverage read" });
  } else if (eventType === "cadence_spec_audit_start") {
    updatePhase("spec_audit", { state: "now", detail: event.message || "Auditing specification" });
  } else if (eventType === "cadence_spec_audit_complete") {
    next.traceability = event.traceability || next.traceability;
    updatePhase("spec_audit", { state: "done", detail: event.message || "Specification audited" });
  } else if (eventType === "cadence_classify_complete") {
    next.findings = event.findings || next.findings;
    updatePhase("report", { state: "now", detail: event.message || "Classifying findings" });
  } else if (eventType === "cadence_report_complete") {
    next.report = event.report || next.report;
    updatePhase("report", { state: "done", detail: event.message || "Report ready" });
  } else if (eventType === "cadence_skipped") {
    next.status = "skipped";
    next.reason = event.message || "Cadence was skipped.";
    next.phases = next.phases.map((phase) => ({
      ...phase,
      state: phase.id === "detect" && next.detection?.run_ready ? "done" : phase.state === "done" ? "done" : "skipped",
    }));
  } else if (eventType === "cadence_complete") {
    next.status = String(event.status || "completed").toLowerCase();
    next.runId = event.run_id || next.runId;
  }

  const finalCadence = eventType === "complete" ? cadenceResultFromExecution(event.result) : null;
  if (finalCadence) {
    next.status = String(finalCadence.status || next.status).toLowerCase();
    next.detection = finalCadence.detection || next.detection;
    next.runId = finalCadence.run_id || next.runId;
    next.reason = finalCadence.reason || next.reason;
    next.summary = finalCadence.analysis?.summary || finalCadence.error || "";
    next.regression = finalCadence.regression || next.regression;
    next.coverage = finalCadence.coverage || next.coverage;
    next.traceability = finalCadence.traceability || next.traceability;
    next.findings = finalCadence.findings || next.findings;
    next.report = finalCadence.report || next.report;
    const commandResults = Array.isArray(finalCadence.commands) ? finalCadence.commands : [];
    commandResults.forEach((commandResult) => {
      const rawPhase = String(commandResult.phase || "");
      const phaseId = rawPhase.startsWith("compile") ? "compile" : rawPhase.startsWith("simulate") ? "simulate" : rawPhase;
      if (!CADENCE_PHASES.some((phase) => phase.id === phaseId)) return;
      updatePhase(phaseId, {
        state: Number(commandResult.returncode) === 0 && !commandResult.timed_out ? "done" : "bad",
        detail: `${phaseId} ${Number(commandResult.returncode) === 0 ? "passed" : "failed"}`,
        command: formatCadenceCommand(commandResult.command),
        returncode: commandResult.returncode,
      });
    });
    if (next.status === "passed") {
      next.phases = next.phases.map((phase) => ({ ...phase, state: "done" }));
    } else if (["needs_action", "needs_review"].includes(next.status)) {
      next.phases = next.phases.map((phase) => ({
        ...phase,
        state: phase.state === "todo" || phase.state === "now" ? "done" : phase.state,
      }));
    } else if (next.status === "skipped") {
      next.phases = next.phases.map((phase) => ({
        ...phase,
        state: phase.id === "detect" && next.detection?.run_ready ? "done" : phase.state === "done" ? "done" : "skipped",
      }));
    } else if (["failed", "error", "unavailable"].includes(next.status)) {
      next.phases = next.phases.map((phase) => ({
        ...phase,
        state: phase.state === "todo" || phase.state === "now" ? "skipped" : phase.state,
      }));
    }
  }

  if (eventType.startsWith("cadence_") && event.message) {
    appendEvent(event.message, {
      passed: event.passed,
      command: formatCadenceCommand(event.command),
    });
  }
  return next;
}

function verdictFromResult(result, projectId = null) {
  if (!result) return { ok: false, title: "Verification finished", sub: "", facts: [] };
  const status = String(result.status || "").toLowerCase();
  const ok = ["passed", "complete", "completed", "success", "pass"].includes(status);
  const facts = [];
  const nestedErrors = [];
  Object.entries(result.results || {}).forEach(([key, val]) => {
    const meta = STRATEGY_META[key]?.label || key;
    facts.push({ k: meta, v: String(val.status || "—") });
    const detail = String(
      val.error
      || val.post_process_warning
      || val.validation?.summary
      || val.compile_gate?.errors?.[0]
      || "",
    ).trim();
    if (detail && /fail|error|missing|not found|no such file|invalid/i.test(detail)) {
      nestedErrors.push(`${meta}: ${detail}`);
    }
    if (key === "uvm" && val.cadence) {
      const cadenceValue = cadenceFactValue(val.cadence);
      facts.push({ k: "Cadence Xcelium", v: cadenceValue });
      const functional = val.cadence.coverage?.functional;
      const code = val.cadence.coverage?.code;
      if (functional) facts.push({ k: "Functional coverage", v: functional.achieved == null ? "missing" : `${functional.achieved}% / ${functional.target}%` });
      if (code) facts.push({ k: "Code coverage", v: code.achieved == null ? "missing" : `${code.achieved}% / ${code.target}%` });
      const cadenceDetail = String(
        val.cadence.error
        || val.cadence.analysis?.summary
        || val.cadence.reason
        || "",
      ).trim();
      if (/fail|error|missing|not found|invalid/i.test(cadenceValue) && cadenceDetail) {
        nestedErrors.push(`Cadence Xcelium: ${cadenceDetail}`);
      }
    }
  });
  let executionBrief = "";
  try {
    executionBrief = JSON.stringify({
      project_id: projectId || null,
      status: result.status,
      summary: result.summary,
      results: result.results,
      run_id: result.run_id ?? result.verification_run_id ?? null,
    });
    if (executionBrief.length > 14000) executionBrief = executionBrief.slice(0, 14000);
  } catch {
    executionBrief = String(result.summary || result.status || "").slice(0, 4000);
  }
  const failedFacts = facts.filter((f) => /fail|error|validation/i.test(String(f.v)));
  const headline = ok
    ? "All checks passed"
    : failedFacts.length === 1
      ? `${failedFacts[0].k}: ${String(failedFacts[0].v).replace(/_/g, " ")}`
      : failedFacts.length > 1
        ? `${failedFacts.length} checks need attention`
        : "Some checks need attention";
  const summary = String(result.summary || "").trim();
  const statusLine = result.status ? `Status: ${result.status}` : "";
  const firstNestedError = nestedErrors[0] || "";
  const sub = ok
    ? summary || statusLine
    : firstNestedError
      ? firstNestedError
    : summary && summary !== headline
      ? summary
      : statusLine;

  const uvmResult = result?.results?.uvm;
  const cadenceSim = uvmResult?.cadence_simulation;
  const cadenceSimStatus = cadenceSim?.status
    || (uvmResult && !cadenceSim ? "not_run" : null);
  const compileTrusted = Boolean(
    uvmResult?.trusted
    || uvmResult?.authoritative_compile
    || uvmResult?.compile_gate?.passed,
  );
  const hasUvmArtifacts = Boolean(
    (uvmResult?.published_artifact_ids || []).length
    || (uvmResult?.file_paths || []).length
    || collectPublishedArtifactIds(result).length,
  );

  let actions;
  if (ok) {
    actions = [
      ...(compileTrusted && hasUvmArtifacts && cadenceSimStatus === "not_run"
        ? [{ id: "run-cadence", label: "Run Cadence simulation", kind: "primary" }]
        : []),
      { id: "promote", label: "Promote design" },
      { id: "upload-log", label: "Upload simulator log" },
    ];
    if (cadenceSimStatus && cadenceSimStatus !== "not_run") {
      actions.unshift({ id: "rerun-cadence", label: "Re-run Cadence", kind: "ghost" });
    }
  } else {
    actions = [
      { id: "upload-log", label: "Upload simulator log", kind: "primary" },
      { id: "explain", label: "Explain failure" },
      { id: "rerun", label: "Re-run" },
    ];
    if (compileTrusted && hasUvmArtifacts) {
      actions.splice(1, 0, { id: "run-cadence", label: "Run Cadence simulation" });
    }
  }

  if (cadenceSimStatus && cadenceSimStatus !== "not_run") {
    facts.push({
      k: "Cadence",
      v: cadenceSimStatus,
    });
  } else if (compileTrusted && hasUvmArtifacts) {
    facts.push({ k: "Cadence", v: "not simulated" });
  }

  return {
    ok,
    title: headline,
    sub,
    facts,
    actions,
    runId: result.run_id ?? result.verification_run_id ?? null,
    verificationSource: "staged_verification",
    executionBrief,
    generatedArtifactIds: collectPublishedArtifactIds(result),
    cadenceSimulationStatus: cadenceSimStatus,
    uvmTopModule: uvmResult?.top_module || "top_tb",
  };
}

function designBugReportFromExecution(result) {
  const cadence = cadenceResultFromExecution(result);
  if (!cadence?.report) return null;
  return {
    status: cadence.status,
    report: cadence.report,
    artifactId: cadence.report.artifact_id || cadence.report.artifact?.id || null,
    counts: cadence.report.counts || {},
    coverage: cadence.coverage || {},
    traceability: cadence.traceability || {},
    findings: cadence.findings || [],
    integrity: cadence.integrity || {},
    runId: cadence.run_id || null,
    artifactIds: collectPublishedArtifactIds(result),
  };
}

function collectPublishedArtifactIds(result) {
  const ids = [];
  const add = (value) => {
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach(add);
      return;
    }
    const id = String(value).trim();
    if (id && !ids.includes(id)) ids.push(id);
  };
  Object.values(result?.results || {}).forEach((section) => {
    if (!section || typeof section !== "object") return;
    add(section.published_artifact_ids);
  });
  add(result?.published_artifact_ids);
  add((result?.artifacts || []).map((artifact) => artifact?.id || artifact?.artifactId));
  return ids;
}

function stagedDownloadFilename(result, verificationType) {
  const type = String(result?.verification_type || verificationType || "verification").replace(/[^a-z0-9_-]+/gi, "_");
  const rev = String(result?.mental_model_revision_id || "").slice(0, 8);
  return `chipverify_${type || "verification"}${rev ? `_${rev}` : ""}_outputs.zip`;
}

function resolveStageFromLegacy(snapshot) {
  // Honour the new richer field if present.
  if (snapshot?.stage) return snapshot.stage;
  // Fall back to legacy currentStage mapping.
  const legacy = snapshot?.currentStage;
  if (legacy === "done" && snapshot?.executionResult) return "completed";
  if (legacy === "execute") return snapshot?.executionResult ? "completed" : "planned";
  if (legacy === "plan" && snapshot?.plan) return "planned";
  if (legacy === "recommend" && snapshot?.recommendation) return "recommended";
  if (legacy === "prepare" && snapshot?.recommendation) return "recommended";
  return "idle";
}

const EMPTY_ITEM_IDS = {
  prep: null,
  rec: null,
  plan: null,
  run: null,
  cadence: null,
  report: null,
  verdict: null,
};

export default function useStagedVerification(projectId, options = {}) {
  const {
    authToken,
    pushItem,
    replaceItem,
    onCancelRun,
    onBeforeStagedHydrate,
    onArtifactsChanged,
  } = options;

  const [stage, setStage] = useState("idle");
  const snapshotRef = useRef({
    stage: "idle",
    mentalModel: null,
    modelRevisionId: null,
    recommendation: null,
    selectedType: null,
    plan: null,
    executionResult: null,
    runCadenceSimulation: false,
    itemIds: { ...EMPTY_ITEM_IDS },
    supersededPlans: [],
    updatedAt: null,
  });
  const projectRef = useRef(projectId || null);
  const hydratedRef = useRef(false);
  const inFlightRef = useRef(false);
  const busyNoticeRef = useRef(0);

  const persist = useCallback(() => {
    if (!projectRef.current) return;
    const snap = snapshotRef.current;
    // Mirror legacy fields so the legacy panel can still read it.
    const legacyStage =
      snap.stage === "completed" ? "done"
      : snap.stage === "executing" ? "execute"
      : snap.stage === "planned" || snap.stage === "refining" ? "plan"
      : snap.stage === "recommended" ? "recommend"
      : snap.stage === "preparing" ? "prepare"
      : "idle";
    saveSnapshot(projectRef.current, {
      // legacy mirror
      currentStage: legacyStage,
      mentalModel: snap.mentalModel,
      modelRevisionId: snap.modelRevisionId,
      recommendation: snap.recommendation,
      selectedType: snap.selectedType,
      plan: snap.plan,
      refineFeedback: "",
      executionResult: snap.executionResult,
      runCadenceSimulation: snap.runCadenceSimulation,
      executionActivity: [],
      executionStepIndex: 0,
      executionEvents: [],
      streamedFiles: [],
      updatedAt: Date.now(),
      // thread-first additions
      stage: snap.stage,
      itemIds: snap.itemIds,
      supersededPlans: snap.supersededPlans,
    });
  }, []);

  const updateSnapshot = useCallback((updater) => {
    snapshotRef.current = { ...snapshotRef.current, ...updater(snapshotRef.current) };
  }, []);

  const resetInternal = useCallback(() => {
    snapshotRef.current = {
      stage: "idle",
      mentalModel: null,
      modelRevisionId: null,
      recommendation: null,
      selectedType: null,
      plan: null,
      executionResult: null,
      runCadenceSimulation: false,
      itemIds: { ...EMPTY_ITEM_IDS },
      supersededPlans: [],
      updatedAt: null,
    };
    setStage("idle");
  }, []);

  const reset = useCallback(() => {
    if (projectRef.current) clearSnapshot(projectRef.current);
    resetInternal();
  }, [resetInternal]);

  const beginInFlight = useCallback((action = "that step") => {
    if (!inFlightRef.current) {
      inFlightRef.current = true;
      return true;
    }
    const now = Date.now();
    if (now - busyNoticeRef.current > 1500) {
      busyNoticeRef.current = now;
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: `Still finishing the current staged-verification step. I'll be ready to ${action} in a moment.`,
      }));
    }
    return false;
  }, [pushItem]);

  const endInFlight = useCallback(() => {
    inFlightRef.current = false;
  }, []);

  // ─────────────────────────────────────────────────────────────────────
  // Card pushers
  // ─────────────────────────────────────────────────────────────────────
  const pushPrep = useCallback((overrides = {}) => {
    const item = makeItem(THREAD_KINDS.STAGED_PREP, {
      summary: overrides.summary || null,
      ready: overrides.ready ?? false,
      loading: overrides.loading ?? false,
      error: overrides.error || null,
    });
    pushItem?.(item);
    updateSnapshot((s) => ({ itemIds: { ...s.itemIds, prep: item.id } }));
    return item.id;
  }, [pushItem, updateSnapshot]);

  const updatePrep = useCallback((patch) => {
    const id = snapshotRef.current.itemIds.prep;
    if (!id || !replaceItem) return;
    replaceItem(id, (prev) => ({ payload: { ...prev.payload, ...patch } }));
  }, [replaceItem]);

  // Push a real MentalModelCard derived from the staged mental_model payload.
  // The card is tagged with _source: "staged" so the workspace's
  // onMentalModelApprove handler can fire staged.recommend() and the
  // onMentalModelCorrect handler can refresh the model via a chat message.
  const pushStagedMentalModel = useCallback((source = "staged") => {
    const fullModel = snapshotRef.current.mentalModel || {};
    const model = fullModel.design || fullModel;
    const description =
      model.description
      || fullModel.description
      || fullModel.top_description
      || fullModel.summary
      || "";
    if (description && !model.description) {
      model.description = description;
    }
    const moduleList = Array.isArray(model.modules)
      ? model.modules.map((m) => ({
          name: typeof m === "string" ? m : (m.name || ""),
          role: typeof m === "string" ? "module" : (m.role || "module"),
          ports: m.ports || [],
        })).filter((m) => m.name)
      : [];
    const ports = Array.isArray(model.ports) ? model.ports : [];
    const inferred = {
      "Top module": model.top_module || "—",
      "Modules": moduleList.length || "—",
      "Ports": ports.length || "—",
      "Parameters": (model.parameters || []).length || "—",
    };
    const summary = snapshotRef.current.recommendation?.design_complexity
      ? `${model.description || "Design overview"} · Complexity ${snapshotRef.current.recommendation.design_complexity}.`
      : model.description || "Here's how I understand the design before we plan tests.";
    const item = makeItem(THREAD_KINDS.MENTAL_MODEL, {
      summary,
      modules: moduleList.length ? moduleList : ports.length ? [{ name: model.top_module || "top", ports }] : [],
      inferred,
      _source: source,
    });
    pushItem?.(item);
    return item.id;
  }, [pushItem]);

  const pushRec = useCallback((recommendation) => {
    const item = makeItem(THREAD_KINDS.STAGED_REC, {
      complexity: recommendation?.design_complexity || null,
      strategies: recommendationStrategies(recommendation),
      picked: null,
      loading: false,
    });
    pushItem?.(item);
    updateSnapshot((s) => ({ itemIds: { ...s.itemIds, rec: item.id } }));
    return item.id;
  }, [pushItem, updateSnapshot]);

  const updateRec = useCallback((patch) => {
    const id = snapshotRef.current.itemIds.rec;
    if (!id || !replaceItem) return;
    replaceItem(id, (prev) => ({ payload: { ...prev.payload, ...patch } }));
  }, [replaceItem]);

  const pushPlan = useCallback((verificationType, plan, opts = {}) => {
    const mentalModel = snapshotRef.current.mentalModel?.design
      ? snapshotRef.current.mentalModel
      : snapshotRef.current.mentalModel;
    const payload = planPayloadFromVerificationApi(plan, verificationType, mentalModel);
    if (opts.locked) {
      payload.actions = [];
    }
    if (snapshotRef.current.runCadenceSimulation) {
      payload.runCadenceSimulation = true;
      payload.cadenceConnected = opts.cadenceConnected;
    }
    const item = makeItem(THREAD_KINDS.PLAN, payload);
    pushItem?.(item);
    updateSnapshot((s) => ({ itemIds: { ...s.itemIds, plan: item.id } }));
    return item.id;
  }, [pushItem, updateSnapshot]);

  const updatePlan = useCallback((patch) => {
    const id = snapshotRef.current.itemIds.plan;
    if (!id || !replaceItem) return;
    replaceItem(id, (prev) => ({ payload: { ...prev.payload, ...patch } }));
  }, [replaceItem]);

  const supersedePreviousPlan = useCallback(() => {
    const id = snapshotRef.current.itemIds.plan;
    if (!id || !replaceItem) return;
    replaceItem(id, (prev) => ({
      payload: { ...prev.payload, superseded: true, refineLoading: false, actions: [] },
    }));
    updateSnapshot((s) => ({
      supersededPlans: [...(s.supersededPlans || []), id],
      itemIds: { ...s.itemIds, plan: null },
    }));
  }, [replaceItem, updateSnapshot]);

  const pushRun = useCallback((verificationType) => {
    const runId = `staged-${Date.now().toString(36)}`;
    const item = makeItem(THREAD_KINDS.RUN, {
      runId,
      title: `Verification run · ${STRATEGY_META[verificationType]?.label || verificationType}`,
      status: "running",
      stages: buildRunStages(verificationType),
      log: [],
    });
    pushItem?.(item);
    updateSnapshot((s) => ({ itemIds: { ...s.itemIds, run: item.id } }));
    return { itemId: item.id, runId };
  }, [pushItem, updateSnapshot]);

  const appendRunEvent = useCallback((event) => {
    const id = snapshotRef.current.itemIds.run;
    if (!id || !replaceItem) return;
    replaceItem(id, (prev) => {
      const log = [
        ...(prev.payload.log || []),
        {
          ts: new Date().toISOString(),
          level: event.type === "error" ? "error" : "info",
          message: event.message || (event.file ? `${event.type}: ${event.file}` : event.type || ""),
        },
      ].slice(-200);
      const stages = advanceStages(prev.payload.stages || [], event);
      return { payload: { ...prev.payload, log, stages } };
    });
  }, [replaceItem]);

  const pushCadence = useCallback((event = null) => {
    const payload = event
      ? reduceCadencePayload(initialCadencePayload(), event)
      : initialCadencePayload();
    const item = makeItem(THREAD_KINDS.CADENCE_RUN, payload);
    pushItem?.(item);
    updateSnapshot((snapshot) => ({ itemIds: { ...snapshot.itemIds, cadence: item.id } }));
    return item.id;
  }, [pushItem, updateSnapshot]);

  const appendCadenceEvent = useCallback((event) => {
    const eventType = String(event?.type || "");
    const hasFinalCadence = eventType === "complete" && cadenceResultFromExecution(event?.result);
    if (!eventType.startsWith("cadence_") && !hasFinalCadence) return;

    let id = snapshotRef.current.itemIds.cadence;
    if (!id) {
      id = pushCadence(event);
      return;
    }
    if (!replaceItem) return;
    replaceItem(id, (previous) => ({
      payload: reduceCadencePayload(previous.payload, event),
    }));
  }, [pushCadence, replaceItem]);

  const finalizeRun = useCallback((status, result = null) => {
    const id = snapshotRef.current.itemIds.run;
    if (!id || !replaceItem) return;
    replaceItem(id, (prev) => {
      const stages = (prev.payload.stages || []).map((s) => ({
        ...s,
        state: s.state === "now" || s.state === "todo"
          ? (status === "completed" || status === "passed" ? "done" : "bad")
          : s.state,
      }));
      const artifactIds = collectPublishedArtifactIds(result);
      const downloadPatch = artifactIds.length
        ? {
            downloadArtifactIds: artifactIds,
            generatedArtifactIds: artifactIds,
            downloadFilename: stagedDownloadFilename(result, snapshotRef.current.selectedType),
          }
        : {};
      const realRunId = result?.run_id ?? result?.verification_run_id ?? prev.payload.runId;
      const strategies = verificationStrategiesFromResult(result);
      return { payload: { ...prev.payload, runId: realRunId, status, stages, strategies, ...downloadPatch } };
    });
  }, [replaceItem]);

  const pushVerdict = useCallback((result) => {
    const item = makeItem(THREAD_KINDS.VERDICT, verdictFromResult(result, projectId));
    pushItem?.(item);
    updateSnapshot((s) => ({ itemIds: { ...s.itemIds, verdict: item.id } }));
  }, [pushItem, projectId, updateSnapshot]);

  const pushDesignBugReport = useCallback((result) => {
    const payload = designBugReportFromExecution(result);
    if (!payload) return null;
    const item = makeItem(THREAD_KINDS.DESIGN_BUG_REPORT, payload);
    pushItem?.(item);
    updateSnapshot((snapshot) => ({ itemIds: { ...snapshot.itemIds, report: item.id } }));
    return item.id;
  }, [pushItem, updateSnapshot]);

  // ─────────────────────────────────────────────────────────────────────
  // Public actions
  // ─────────────────────────────────────────────────────────────────────
  const prepare = useCallback(async () => {
    if (!projectId) {
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: "Pick a project first, then I can plan a verification.",
      }));
      return;
    }
    if (!beginInFlight("prepare the design")) return;
    setStage("preparing");
    updateSnapshot(() => ({ stage: "preparing" }));
    pushItem?.(makeItem(THREAD_KINDS.SAID, {
      text: "Let's plan this carefully — I'll review your spec and RTL before we run anything.",
    }));
    pushPrep({ loading: true, ready: false });
    persist();
    try {
      const result = await prepareVerification(projectId, {}, authToken);
      const model = normalizeModel(result);
      const summary = modelSummary(model, result.recommendation);
      updateSnapshot(() => ({
        mentalModel: model,
        modelRevisionId: result.mental_model_revision_id || null,
        recommendation: result.recommendation || null,
      }));
      updatePrep({ loading: false, ready: true, summary, error: null });
      track(AnalyticsEvents.STAGED_PREPARE_COMPLETED, { project_id: projectId });
      persist();
    } catch (e) {
      captureError(e, { area: "staged.prepare", project_id: projectId });
      updatePrep({ loading: false, ready: false, error: e?.message || "Prepare failed." });
      setStage("idle");
      updateSnapshot(() => ({ stage: "idle" }));
      persist();
    } finally {
      endInFlight();
    }
  }, [projectId, authToken, pushItem, pushPrep, updatePrep, updateSnapshot, persist, beginInFlight, endInFlight]);

  // Step 1 of recommend: show the mental model so the user can catch any
  // misunderstanding BEFORE we propose tests. The strategy cards only land
  // after the user approves the model.
  const reviewMentalModel = useCallback((options = {}) => {
    const source = options.source || "staged";
    if (!snapshotRef.current.mentalModel) {
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: "I'll scan your design first, then show how I understand it.",
      }));
      void prepare().then(() => {
        if (snapshotRef.current.mentalModel) pushStagedMentalModel(source);
      });
      return;
    }
    pushStagedMentalModel(source);
  }, [pushStagedMentalModel, pushItem, prepare]);

  const ensureRecommendation = useCallback(async () => {
    if (!projectId) return false;
    if (snapshotRef.current.recommendation) return true;
    if (!beginInFlight("load verification strategies")) return false;
    try {
      const result = await prepareVerification(projectId, {}, authToken);
      const model = normalizeModel(result);
      updateSnapshot((s) => ({
        mentalModel: model || s.mentalModel,
        modelRevisionId: result.mental_model_revision_id || s.modelRevisionId,
        recommendation: result.recommendation || null,
      }));
      return Boolean(snapshotRef.current.recommendation);
    } catch (e) {
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: `Couldn't load verification strategies: ${e?.message || "unknown error"}.`,
      }));
      return false;
    } finally {
      endInFlight();
    }
  }, [projectId, authToken, pushItem, updateSnapshot, beginInFlight, endInFlight]);

  const recommend = useCallback(async () => {
    if (!projectId) {
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: "Pick a project first, then we can plan verification.",
      }));
      return;
    }

    if (!snapshotRef.current.recommendation) {
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: "Loading verification strategies for your design…",
      }));
      let ready = await ensureRecommendation();
      if (!ready && !snapshotRef.current.mentalModel && !inFlightRef.current) {
        await prepare();
        ready = Boolean(snapshotRef.current.recommendation);
      }
      if (!ready) {
        pushItem?.(makeItem(THREAD_KINDS.SAID, {
          text: "I couldn't load strategy recommendations yet. Try **Build mental model** first, then start verification again.",
        }));
        return;
      }
    }

    const recommendation = snapshotRef.current.recommendation;
    setStage("recommended");
    updateSnapshot(() => ({ stage: "recommended" }));
    pushRec(recommendation);
    persist();
  }, [projectId, pushItem, pushRec, updateSnapshot, persist, ensureRecommendation, prepare]);

  /** Prepare (scan spec+RTL) then surface the mental-model card in-thread. */
  const prepareThenReviewMentalModel = useCallback(async (source = "design-gate") => {
    await prepare();
    if (snapshotRef.current.mentalModel) {
      reviewMentalModel({ source });
    }
  }, [prepare, reviewMentalModel]);

  const plan = useCallback(async (strategyId) => {
    if (!projectId || !strategyId) return;
    if (!beginInFlight("generate the plan")) return;
    track(AnalyticsEvents.STAGED_STRATEGY_SELECTED, {
      project_id: projectId,
      strategy_id: strategyId,
    });
    updateRec({ picked: strategyId, loading: true });
    updateSnapshot(() => ({ selectedType: strategyId }));
    try {
      const result = await generatePlan(projectId, {
        verificationType: strategyId,
        mentalModelRevisionId: snapshotRef.current.modelRevisionId,
      }, authToken);
      updateSnapshot(() => ({ plan: result.plan || null, stage: "planned" }));
      setStage("planned");
      updateRec({ loading: false });
      pushPlan(strategyId, result.plan || {});
      if (result?.plan_artifact || result?.artifacts?.length) {
        try {
          await Promise.resolve(onArtifactsChanged?.(result));
        } catch {
          // The plan markdown is already stored server-side; explorer refresh is best-effort.
        }
      }
      track(AnalyticsEvents.STAGED_PLAN_GENERATED, {
        project_id: projectId,
        strategy_id: strategyId,
      });
      persist();
    } catch (e) {
      captureError(e, { area: "staged.plan", project_id: projectId, strategy_id: strategyId });
      updateRec({ loading: false });
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: `Couldn't draft the plan: ${e?.message || "unknown error"}.`,
      }));
    } finally {
      endInFlight();
    }
  }, [projectId, authToken, pushPlan, updateRec, updateSnapshot, pushItem, persist, beginInFlight, endInFlight, onArtifactsChanged]);

  const refine = useCallback(async (prompt) => {
    if (!prompt?.trim() || !projectId) return;
    const snap = snapshotRef.current;
    if (!snap.plan || !snap.selectedType) return;
    if (!beginInFlight("refine the plan")) return;
    updatePlan({ refineLoading: true });
    setStage("refining");
    updateSnapshot(() => ({ stage: "refining" }));
    try {
      const result = await refinePlan(projectId, {
        verificationType: snap.selectedType,
        planJson: snap.plan,
        feedback: prompt,
      }, authToken);
      const newPlan = result.plan || snap.plan;
      supersedePreviousPlan();
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: `Refined the plan with: "${prompt.trim()}"`,
      }));
      updateSnapshot(() => ({ plan: newPlan, stage: "planned" }));
      setStage("planned");
      pushPlan(snap.selectedType, newPlan);
      if (result?.plan_artifact || result?.artifacts?.length) {
        try {
          await Promise.resolve(onArtifactsChanged?.(result));
        } catch {
          // The refined plan markdown is already stored server-side; explorer refresh is best-effort.
        }
      }
      persist();
    } catch (e) {
      updatePlan({ refineLoading: false });
      setStage("planned");
      updateSnapshot(() => ({ stage: "planned" }));
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: `Couldn't refine the plan: ${e?.message || "unknown error"}.`,
      }));
    } finally {
      endInFlight();
    }
  }, [projectId, authToken, pushItem, pushPlan, supersedePreviousPlan, updatePlan, updateSnapshot, persist, beginInFlight, endInFlight, onArtifactsChanged]);

  const approveAndExecute = useCallback(async () => {
    if (!projectId || !snapshotRef.current.plan || !snapshotRef.current.selectedType) return;
    if (!beginInFlight("execute the approved plan")) return;
    const snap = snapshotRef.current;
    // Lock the plan card.
    updatePlan({ actions: [], superseded: false });
    setStage("executing");
    updateSnapshot(() => ({ stage: "executing" }));
    track(AnalyticsEvents.STAGED_EXECUTE_STARTED, {
      project_id: projectId,
      strategy_id: snap.selectedType,
    });
    pushItem?.(makeItem(THREAD_KINDS.SAID, { text: "Plan approved — implementing verification now." }));
    pushRun(snap.selectedType);
    persist();
    try {
      const approvedPlan = { ...snap.plan };
      if (snap.runCadenceSimulation && snap.selectedType === "uvm") {
        approvedPlan.uvm = {
          ...(approvedPlan.uvm || {}),
          run_cadence_simulation: true,
        };
      }
      const result = await executeVerificationStream(
        projectId,
        {
          verificationType: snap.selectedType,
          mentalModelRevisionId: snap.modelRevisionId,
          approvedPlan,
        },
        authToken,
        (event) => {
          appendRunEvent(event);
          appendCadenceEvent(event);
        },
      );
      updateSnapshot(() => ({ executionResult: result, stage: "completed" }));
      setStage("completed");
      track(AnalyticsEvents.STAGED_EXECUTE_COMPLETED, {
        project_id: projectId,
        strategy_id: snap.selectedType,
        status: String(result?.status || "completed").toLowerCase(),
      });
      finalizeRun(String(result?.status || "completed").toLowerCase(), result);
      try {
        await Promise.resolve(onArtifactsChanged?.(result));
      } catch {
        // Artifact refresh is best-effort; the generated files are still stored
        // server-side even if the explorer refresh fails.
      }
      pushDesignBugReport(result);
      pushVerdict(result);
      persist();
    } catch (e) {
      finalizeRun("failed");
      track(AnalyticsEvents.STAGED_EXECUTE_FAILED, {
        project_id: projectId,
        strategy_id: snap.selectedType,
        error: e?.message || "unknown error",
      });
      captureError(e, { area: "staged.execute", project_id: projectId });
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: `Verification failed to execute: ${e?.message || "unknown error"}.`,
      }));
      updateSnapshot(() => ({ stage: "failed" }));
      setStage("failed");
      persist();
    } finally {
      endInFlight();
    }
  }, [projectId, authToken, pushItem, pushRun, appendRunEvent, appendCadenceEvent, finalizeRun, pushDesignBugReport, pushVerdict, updatePlan, updateSnapshot, persist, beginInFlight, endInFlight, onArtifactsChanged]);

  const cancel = useCallback(() => {
    const runId = snapshotRef.current.itemIds.run;
    if (runId && onCancelRun) onCancelRun(runId);
  }, [onCancelRun]);

  // ─────────────────────────────────────────────────────────────────────
  // Hydration on project switch
  // ─────────────────────────────────────────────────────────────────────
  useEffect(() => {
    projectRef.current = projectId || null;
    hydratedRef.current = false;
    if (!projectId) {
      resetInternal();
      hydratedRef.current = true;
      return;
    }
    const stored = loadSnapshot(projectId);
    if (!stored) {
      resetInternal();
      hydratedRef.current = true;
      return;
    }
    const resolved = resolveStageFromLegacy(stored);
    snapshotRef.current = {
      stage: resolved,
      mentalModel: stored.mentalModel || null,
      modelRevisionId: stored.modelRevisionId || null,
      recommendation: stored.recommendation || null,
      selectedType: stored.selectedType || null,
      plan: stored.plan || null,
      executionResult: stored.executionResult || null,
      runCadenceSimulation: Boolean(stored.runCadenceSimulation),
      itemIds: { ...EMPTY_ITEM_IDS },
      supersededPlans: [],
      updatedAt: stored.updatedAt || null,
    };
    setStage(resolved);

    if (resolved !== "idle" && pushItem) {
      // Drop any stale staged rows still in thread extras so we do not stack copies
      // under the replay from localStorage (extras persist; staged snapshot also replays).
      try {
        onBeforeStagedHydrate?.();
      } catch {
        /* non-fatal */
      }
      const when = stored.updatedAt
        ? new Date(stored.updatedAt).toLocaleString()
        : "earlier";
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: `Resumed verification plan from ${when}.`,
      }));
      // Re-hydrate the visible card stack for the resolved stage.
      if (["preparing", "recommended", "planned", "refining", "executing", "completed", "failed"].includes(resolved)) {
        pushPrep({ loading: false, ready: !!stored.recommendation, summary: modelSummary(stored.mentalModel, stored.recommendation) });
      }
      if (["recommended", "planned", "refining", "executing", "completed", "failed"].includes(resolved) && stored.recommendation) {
        pushRec(stored.recommendation);
        if (stored.selectedType) updateRec({ picked: stored.selectedType });
      }
      if (["planned", "refining", "executing", "completed", "failed"].includes(resolved) && stored.plan && stored.selectedType) {
        const locked = ["executing", "completed", "failed"].includes(resolved);
        pushPlan(stored.selectedType, stored.plan, { locked });
      }
      if (["completed", "failed"].includes(resolved) && stored.executionResult) {
        if (cadenceResultFromExecution(stored.executionResult)) {
          pushCadence({ type: "complete", result: stored.executionResult });
        }
        pushDesignBugReport(stored.executionResult);
        pushVerdict(stored.executionResult);
      }
    }
    hydratedRef.current = true;
    // We intentionally only re-run on project change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const ingestMentalModelFromBuild = useCallback((apiResult) => {
    const model = normalizeModel(apiResult);
    if (!model) return;
    updateSnapshot((s) => ({
      mentalModel: model,
      modelRevisionId:
        apiResult?.mental_model?.id
        || apiResult?.mental_model_revision_id
        || s.modelRevisionId,
    }));
    persist();
  }, [updateSnapshot, persist]);

  const setRunCadenceSimulation = useCallback((value) => {
    updateSnapshot(() => ({ runCadenceSimulation: Boolean(value) }));
    updatePlan({ runCadenceSimulation: Boolean(value) });
    persist();
  }, [updateSnapshot, updatePlan, persist]);

  return {
    stage,
    snapshot: snapshotRef.current,
    prepare,
    prepareThenReviewMentalModel,
    reviewMentalModel,
    recommend,
    plan,
    refine,
    approveAndExecute,
    cancel,
    reset,
    ingestMentalModelFromBuild,
    setRunCadenceSimulation,
  };
}
