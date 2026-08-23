import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { expandPersistedAssistantContent } from "./chipixFences";
import { THREAD_KINDS, makeItem } from "./types";
import {
  extractVerificationStrategies,
  getRunDashboard,
  hasMeasurableVerificationResults,
  hasVerificationPhaseEvidence,
} from "./verificationRunMetrics";

const EXTRAS_STORAGE_PREFIX = "chipverify.threadExtras.v1";

function extrasStorageKey(projectId, threadId) {
  return projectId && threadId ? `${EXTRAS_STORAGE_PREFIX}:${projectId}:${threadId}` : null;
}

function loadExtrasFromStorage(projectId, threadId) {
  const k = extrasStorageKey(projectId, threadId);
  if (!k || typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(k);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveExtrasToStorage(projectId, threadId, list) {
  const k = extrasStorageKey(projectId, threadId);
  if (!k || typeof window === "undefined") return;
  try {
    const filtered = (list || []).filter((e) => {
      if (!e || e.kind === THREAD_KINDS.THINKING) return false;
      // Assistant asks/plans are persisted in chat_messages inside ```chipix``` fences;
      // hydrating those rows recreates cards — storing duplicates here causes double cards after reload.
      if (e.kind === THREAD_KINDS.ASK || e.kind === THREAD_KINDS.PLAN) return false;
      // Staged verification cards are rebuilt from chipverify.stagedVerification.v1 on project load;
      // persisting them here stacks duplicates with the hydration replay.
      if (e.kind === THREAD_KINDS.STAGED_PREP || e.kind === THREAD_KINDS.STAGED_REC) return false;
      if (e.kind === THREAD_KINDS.CADENCE_RUN) return false;
      if (e.kind === THREAD_KINDS.DESIGN_BUG_REPORT) return false;
      if (e.kind === THREAD_KINDS.VERDICT && e.payload?.verificationSource === "staged_verification") {
        return false;
      }
      return true;
    });
    window.localStorage.setItem(k, JSON.stringify(filtered));
  } catch {
    // quota / private mode
  }
}

/**
 * Normalize the various backend states from DesktopApp into a single
 * ordered ThreadItem[] timeline.
 *
 * Inputs:
 *   - activeProject              project record
 *   - artifactState              { artifacts:{spec,rtl}, active, content }
 *   - activeRunId / activeRunStatus
 *   - chatMessages               legacy chat thread messages (optional)
 *   - extras                     ad-hoc items appended by the workspace
 *     (asks, said, thinking, mental-model cards inserted from RTL stream)
 *
 * The hook also exposes mutators so consumers can push/replace items.
 */
export default function useProjectThreadState({
  activeProject,
  /** Prefer explicit id so thread persistence does not depend on full project object identity */
  projectId: projectIdProp,
  activeThreadId = null,
  artifactState,
  activeRunId,
  activeRunStatus,
  chatMessages = [],
  rtlDesignState,
  projectAgents = [],
  // True only when the currently-active run was started from THIS thread.
  // Without this, a fresh thread would inherit the project's last run
  // (Run/Verdict/AgentBeat) the moment the user types their first message.
  runBelongsToThread = false,
}) {
  const projectId = projectIdProp ?? activeProject?.id ?? null;
  const [extras, setExtras] = useState([]); // additional thread items (user msgs, ask, etc.)
  const extrasRef = useRef(extras);
  extrasRef.current = extras;

  const persistCtxRef = useRef({ pid: null, tid: null });

  // Persist timeline extras per (project, thread). Flush previous thread on switch;
  // reload saved extras for the newly active thread so chip/file rows survive navigation.
  useEffect(() => {
    const pid = projectId || null;
    const tid = activeThreadId || null;
    const prev = persistCtxRef.current;

    if (prev.pid && prev.tid && (prev.pid !== pid || prev.tid !== tid)) {
      saveExtrasToStorage(prev.pid, prev.tid, extrasRef.current);
    }

    persistCtxRef.current = { pid, tid };

    if (pid && tid) {
      setExtras(loadExtrasFromStorage(pid, tid));
    } else {
      setExtras([]);
    }
  }, [projectId, activeThreadId]);

  useEffect(() => {
    if (!projectId || !activeThreadId) return;
    const handle = setTimeout(() => {
      saveExtrasToStorage(projectId, activeThreadId, extras);
    }, 350);
    return () => clearTimeout(handle);
  }, [extras, projectId, activeThreadId]);

  const pushItem = useCallback((item) => {
    setExtras((prev) => [...prev, item]);
  }, []);

  const replaceItem = useCallback((id, updater) => {
    setExtras((prev) =>
      prev.map((item) => (item.id === id ? { ...item, ...updater(item) } : item)),
    );
  }, []);

  const removeItem = useCallback((id) => {
    setExtras((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const clearItems = useCallback(() => setExtras([]), []);

  const setExtrasDirect = useCallback((updater) => {
    setExtras(typeof updater === "function" ? updater : () => updater);
  }, []);

  // Chat-thread messages → user / said items (presentation only).
  const chatItems = useMemo(() => {
    let activeTurnId = null;
    const rows = Array.isArray(chatMessages) ? chatMessages : [];

    return rows
      .filter((m) => m && (m.content || m.text))
      .flatMap((m) => {
        const role = String(m.role || m.author || "assistant").toLowerCase();
        const text = String(m.content || m.text || "").trim();
        const ts = m.created_at || m.timestamp || new Date().toISOString();
        const messageId = m.id ? String(m.id) : null;

        if (role === "tool") return [];

        if (role === "user") {
          activeTurnId = messageId ? `turn-${messageId}` : null;
          return [
            makeItem(THREAD_KINDS.USER, { text, tokenUsage: m.token_usage || null }, {
              id: `chat-${messageId || ts}`,
              ts,
              turnId: activeTurnId || undefined,
              backendMessageId: messageId || undefined,
            }),
          ];
        }

        const expanded = expandPersistedAssistantContent(text, {
          ts,
          messageId,
          turnId: activeTurnId || undefined,
          who: role === "system" ? "system" : "assistant",
        });

        if (expanded.length) {
          return expanded.map((item) => (
            item.kind === THREAD_KINDS.SAID
              ? { ...item, payload: { ...item.payload, tokenUsage: m.token_usage || null } }
              : item
          ));
        }

        return [
          makeItem(THREAD_KINDS.SAID, { text, who: role, tokenUsage: m.token_usage || null }, {
            id: `chat-${messageId || ts}`,
            ts,
            turnId: activeTurnId || undefined,
            backendMessageId: messageId || undefined,
          }),
        ];
      });
  }, [chatMessages]);

  // Active run → single Run card.
  const runItem = useMemo(() => {
    if (!activeRunId || !activeRunStatus) return null;
    const status = String(activeRunStatus.status || "running").toLowerCase();
    const logs = Array.isArray(activeRunStatus.logs) ? activeRunStatus.logs : [];
    const events = Array.isArray(activeRunStatus.events) ? activeRunStatus.events : logs;
    const title =
      activeRunStatus.title
      || activeRunStatus.summary
      || `Verification run · ${String(activeRunId).slice(0, 8)}`;

    const stages = buildStagesFromEvents(events, status);
    const currentPhase = detectCurrentPhase(events, status);
    const ts = activeRunStatus.created_at || new Date().toISOString();
    const dash = getRunDashboard(activeRunStatus);
    const kpis = dash?.kpis || {};
    const covRaw = dash?.coverage?.line ?? dash?.coverage?.functional ?? null;
    const runKpis = {
      total: kpis.total ?? 0,
      passed: kpis.passed ?? 0,
      failed: kpis.failed ?? 0,
      coverage_pct: covRaw != null
        ? (Number(covRaw) <= 1 ? Math.round(Number(covRaw) * 100) : Math.round(Number(covRaw)))
        : null,
    };
    const strategies = extractVerificationStrategies(activeRunStatus);

    return makeItem(
      THREAD_KINDS.RUN,
      {
        runId: activeRunId,
        title,
        status,
        stages,
        currentPhase, // { id, label, idx } or null
        phaseCount: VERIFICATION_PHASES.length,
        runKpis,
        strategies,
        log: events.map((e) => ({
          ts: e.created_at || e.timestamp,
          level: String(e.level || "info").toLowerCase(),
          message: prettifyRunMessage(e.message),
        })),
      },
      { id: `run-${activeRunId}`, ts },
    );
  }, [activeRunId, activeRunStatus]);

  // Terminal-status verdict + first-failure why card derived from the run.
  // Single card per kind, keyed by run id, so React reconciles updates instead
  // of pushing duplicates each time activeRunStatus mutates.
  const verdictItems = useMemo(() => {
    if (!activeRunId || !activeRunStatus) return [];
    const status = String(activeRunStatus.status || "").toLowerCase();
    const terminal = ["completed", "passed", "failed", "cancelled", "interrupted"].includes(status);
    if (!terminal) return [];

    const dash = getRunDashboard(activeRunStatus) || {};
    const kpis = dash.kpis || {};
    const total = kpis.total ?? null;
    const passed = kpis.passed ?? 0;
    const failed = kpis.failed ?? 0;
    const cov = dash.coverage?.line ?? dash.coverage?.functional ?? null;
    const covPct = cov != null
      ? (Number(cov) <= 1 ? Math.round(Number(cov) * 100) : Math.round(Number(cov)))
      : null;
    const runtime = activeRunStatus.execution_time
      ?? activeRunStatus.runtime_seconds
      ?? dash.runtime_seconds
      ?? null;
    const runtimeStr = (() => {
      if (runtime == null) return null;
      const s = Number(runtime);
      if (!Number.isFinite(s) || s <= 0) return null;
      if (s < 60) return `${Math.round(s)}s`;
      const m = Math.floor(s / 60);
      const r = Math.round(s - m * 60);
      return `${m}m ${String(r).padStart(2, "0")}s`;
    })();

    // Cancelled is NOT a pass — the user (or the system) aborted before a
    // verdict was reached. Treat it as a non-positive terminal so we don't
    // offer "Promote" on a half-finished run.
    const hasDashboardEvidence = hasMeasurableVerificationResults(dash);
    const hasPhaseEvidence = hasVerificationPhaseEvidence(activeRunStatus);
    const hasRunEvidence = hasDashboardEvidence || hasPhaseEvidence;

    const ok = !["failed", "interrupted", "cancelled"].includes(status) && hasDashboardEvidence;
    const failures = Array.isArray(dash.failures) ? dash.failures : [];
    const firstFailure = failures[0] || null;

    const title = ok
      ? total != null
        ? `All ${total} scenarios passed.`
        : "Verification passed."
      : !hasRunEvidence && ["completed", "passed"].includes(status)
        ? "Run finished without verification metrics"
      : hasPhaseEvidence && !hasDashboardEvidence
        ? "Verification finished — detailed scenario results pending"
      : firstFailure?.scenario_name
        ? `Failed: ${firstFailure.scenario_name}`
        : total != null
          ? `${failed} of ${total} scenarios failed`
          : "Verification failed";

    const sub = ok
      ? covPct != null ? `Coverage ${covPct}%${runtimeStr ? ` · ${runtimeStr}` : ""}` : runtimeStr || ""
      : firstFailure?.root_cause || firstFailure?.error_message
        || (runtimeStr ? `Failed after ${runtimeStr}` : "");

    const facts = [];
    if (total != null) facts.push({ k: "Scenarios", v: `${passed}/${total}` });
    if (covPct != null) facts.push({ k: "Coverage", v: `${covPct}%` });
    if (runtimeStr) facts.push({ k: "Runtime", v: runtimeStr });

    const actions = ok
      ? [
          { id: "promote", label: "Promote" },
          { id: "rerun", label: "Re-run" },
        ]
      : [
          { id: "explain", label: "Explain the failure" },
          { id: "open-dashboard", label: "Project dashboard" },
          { id: "rerun", label: "Re-run" },
        ];

    let agentRunDigest = "";
    try {
      agentRunDigest = JSON.stringify({
        verification_source: "pipeline_run",
        pipeline_status: status,
        run_id: activeRunId,
        summary_title: title,
        summary_sub: sub,
        facts,
        dashboard: {
          kpis,
          overall_status: dash.overall_status,
          failures: failures.slice(0, 8),
          types_summary: dash.types_summary || dash.verification_types || null,
        },
        verification_summary_raw:
          typeof activeRunStatus.verification_summary === "string"
            ? activeRunStatus.verification_summary.slice(0, 6000)
            : null,
      });
      if (agentRunDigest.length > 14000) agentRunDigest = agentRunDigest.slice(0, 14000);
    } catch {
      agentRunDigest = "";
    }

    const items = [
      makeItem(
        THREAD_KINDS.VERDICT,
        {
          ok,
          title,
          sub,
          facts,
          runId: activeRunId,
          actions,
          verificationSource: "pipeline_run",
          _agentRunDigest: agentRunDigest,
        },
        {
          id: `verdict-${activeRunId}`,
          ts: activeRunStatus.completed_at || activeRunStatus.updated_at || activeRunStatus.created_at || new Date().toISOString(),
        },
      ),
    ];

    if (!ok && firstFailure) {
      const file = firstFailure.file || firstFailure.source_file;
      const line = firstFailure.line || firstFailure.source_line;
      const body = firstFailure.root_cause
        || firstFailure.error_message
        || "Scenario failed without a parseable reason.";
      items.push(makeItem(
        THREAD_KINDS.WHY,
        {
          title: firstFailure.scenario_name || firstFailure.name || "What went wrong",
          body,
          quote: firstFailure.code_excerpt
            ? { loc: file && line ? `${file}:${line}` : file || "", code: firstFailure.code_excerpt }
            : undefined,
          openLink: file ? { name: file, line } : undefined,
          file,
          line,
        },
        {
          id: `why-${activeRunId}-0`,
          ts: activeRunStatus.completed_at || activeRunStatus.updated_at || new Date().toISOString(),
        },
      ));

      const signals = Array.isArray(firstFailure.signals)
        ? firstFailure.signals
        : Array.isArray(firstFailure.waveform?.signals)
          ? firstFailure.waveform.signals
          : null;
      if (signals && signals.length) {
        const cleanSignals = signals
          .map((s) => {
            const values = Array.isArray(s.values) ? s.values
              : Array.isArray(s.samples) ? s.samples
              : Array.isArray(s.transitions) ? s.transitions.map((t) => t.val ?? t.value ?? 0)
              : null;
            return values ? { name: s.name || s.signal || "sig", values } : null;
          })
          .filter(Boolean);
        if (cleanSignals.length) {
          items.push(makeItem(
            THREAD_KINDS.WAVEFORM,
            {
              signals: cleanSignals,
              failureAt: firstFailure.failure_at || firstFailure.timestamp_ns ? (firstFailure.failure_at || `t=${firstFailure.timestamp_ns}ns`) : null,
              note: firstFailure.waveform_note || null,
            },
            {
              id: `wave-${activeRunId}-0`,
              ts: activeRunStatus.completed_at || activeRunStatus.updated_at || new Date().toISOString(),
            },
          ));
        }
      }
    }

    // Per-strategy summary — only when MULTIPLE strategies ran AND results
    // are mixed. Single-strategy and uniformly-pass/fail runs are already
    // summarized by the VerdictCard; emitting a strategy card there would be
    // redundant chrome.
    const strategies = extractVerificationStrategies(activeRunStatus);
    const hasStrategyCounts = strategies.some(
      (s) => s.status === "running" || Number(s.total) > 0 || Number(s.passed) > 0 || Number(s.failed) > 0,
    );
    if (hasStrategyCounts) {
      items.push(makeItem(
        THREAD_KINDS.STRATEGY_RESULT,
        {
          title: status === "running" || status === "queued" ? "Verification in progress" : "Results by strategy",
          strategies,
        },
        {
          id: `strat-${activeRunId}`,
          ts: activeRunStatus.completed_at || activeRunStatus.updated_at || new Date().toISOString(),
        },
      ));
    }

    return items;
  }, [activeRunId, activeRunStatus]);

  // Live pipeline telemetry → AgentBeat card while a run is in flight, then a
  // collapsed one-liner summary once the run reaches a terminal status.
  const agentBeatItem = useMemo(() => {
    if (!activeRunId || !activeRunStatus) return null;
    const status = String(activeRunStatus.status || "").toLowerCase();
    const agents = Array.isArray(projectAgents) ? projectAgents : [];
    // Surface the agent beat for ANY terminal status too (even with no
    // agent rows), otherwise a failed run with empty projectAgents shows
    // nothing in the thread except the verdict — the user loses the
    // "what was the pipeline doing when it died?" signal.
    const terminalStatuses = ["completed", "passed", "failed", "cancelled", "interrupted"];
    const isTerminal = terminalStatuses.includes(status);
    if (!agents.length && !["running", "queued"].includes(status) && !isTerminal) return null;
    const startedAt = activeRunStatus.started_at || activeRunStatus.created_at;
    const startedMs = startedAt ? new Date(startedAt).getTime() : null;
    const terminal = ["completed", "passed", "failed", "cancelled", "interrupted"].includes(status);
    return makeItem(
      THREAD_KINDS.AGENT_BEAT,
      {
        runId: activeRunId,
        runStatus: status,
        startedAt: startedMs,
        agents,
        collapsed: terminal,
      },
      { id: `agentbeat-${activeRunId}`, ts: startedAt || new Date().toISOString() },
    );
  }, [activeRunId, activeRunStatus, projectAgents]);

  // Active artifacts → file cards in the welcome state (if no other context).
  const artifactItems = useMemo(() => {
    const items = [];
    const artifacts = artifactState?.artifacts || {};
    const ts = artifactState?.active?.activated_at || new Date(0).toISOString();

    (artifacts.rtl || []).slice(0, 3).forEach((art) => {
      items.push(
        makeItem(
          THREAD_KINDS.FILE,
          {
            artifactId: art.id,
            name: art.filename || "rtl source",
            summary: art.metadata?.summary || "RTL artifact",
            language: "verilog",
            addedLines: art.line_count,
          },
          { id: `art-${art.id}`, ts: art.created_at || ts },
        ),
      );
    });

    return items;
  }, [artifactState]);

  // RTL design events from the design stream — projected into thread items.
  const rtlItems = useMemo(() => {
    if (!rtlDesignState?.threadItems) return [];
    return rtlDesignState.threadItems;
  }, [rtlDesignState]);

  // hasThreadActivity — "has the user engaged this conversation yet?".
  // Project-level state (run/agent/verdict/artifacts) is summary, not history,
  // and must NOT auto-populate a fresh thread. It only joins the timeline once
  // the user has actually started talking here.
  const CONVERSATION_KINDS = new Set([
    THREAD_KINDS.USER,
    THREAD_KINDS.SAID,
    THREAD_KINDS.THINKING,
    THREAD_KINDS.ASK,
    THREAD_KINDS.PLAN,
    THREAD_KINDS.FILE,
    THREAD_KINDS.DIFF,
    THREAD_KINDS.BREAK,
    THREAD_KINDS.MENTAL_MODEL,
    THREAD_KINDS.STAGED_PREP,
    THREAD_KINDS.STAGED_REC,
    THREAD_KINDS.TASK_LIST,
  ]);
  const hasThreadActivity = useMemo(() => {
    if (chatItems.length > 0) return true;
    if (rtlItems.length > 0) return true;
    return extras.some((e) => e && CONVERSATION_KINDS.has(e.kind));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatItems.length, rtlItems.length, extras]);

  // Compose & sort.
  // Project-derived items (runItem / agentBeatItem / verdictItems) ONLY join
  // when the thread is already a conversation. Otherwise an empty new thread
  // would inherit the project's last-run history and look pre-populated.
  // artifactItems are dropped entirely — files live in the drawer.
  const items = useMemo(() => {
    const merged = [...chatItems, ...rtlItems, ...extras];
    // Run/verdict/agent-beat cards belong to the thread that started the run.
    // They join the timeline only when (a) the thread has conversation activity
    // AND (b) the active run was started from THIS thread. The second check is
    // what stops "Verification completed" leaking into a brand-new chat that
    // happens to be in a project with prior runs.
    if (hasThreadActivity && runBelongsToThread) {
      if (runItem) merged.push(runItem);
      if (agentBeatItem) merged.push(agentBeatItem);
      if (verdictItems.length) merged.push(...verdictItems);
    }
    merged.sort((a, b) => {
      const ta = new Date(a.ts || 0).getTime();
      const tb = new Date(b.ts || 0).getTime();
      return ta - tb;
    });
    // Same id can appear twice (e.g. extras localStorage + staged hydration replay); keep first in time order.
    const seenIds = new Set();
    const deduped = [];
    for (const it of merged) {
      if (!it?.id) {
        deduped.push(it);
        continue;
      }
      if (seenIds.has(it.id)) continue;
      seenIds.add(it.id);
      deduped.push(it);
    }
    return deduped;
  }, [chatItems, rtlItems, extras, runItem, agentBeatItem, verdictItems, hasThreadActivity, runBelongsToThread]);

  return {
    items,
    hasThreadActivity,
    pushItem,
    replaceItem,
    removeItem,
    clearItems,
    setExtras: setExtrasDirect,
    extras,
  };
}

const STAGE_DEFS = [
  { id: "queued", label: "Queued", match: ["queued"] },
  { id: "lint", label: "Lint", match: ["lint", "linting"] },
  { id: "compile", label: "Compile", match: ["compile", "elaborate"] },
  { id: "simulate", label: "Simulate", match: ["simulate", "simulating", "running", "sim"] },
  { id: "verdict", label: "Verdict", match: ["verdict", "completed", "failed"] },
];

// Finer 10-phase pipeline narrative (ported from the working-demo AgentPanel).
// Each phase is detected by a regex; the LATEST matching phase in the event
// stream is the "current" one. This complements STAGE_DEFS — the 5-stage pill
// row is the high-level pass/fail signal, this is the friendly per-step label.
const VERIFICATION_PHASES = [
  { id: "queued",      label: "Queued",                idx: 0,  re: /queued|waiting to start/i },
  { id: "scan",        label: "Scanning project",      idx: 1,  re: /phase\s*0|project scan|scanning/i },
  { id: "understand",  label: "Understanding the spec",idx: 2,  re: /phase\s*1|understand/i },
  { id: "think",       label: "Reasoning about scenarios", idx: 3, re: /phase\s*2|thinking|reasoning|debate/i },
  { id: "generate",    label: "Generating testbench",  idx: 4,  re: /phase\s*3(?!\.5)|generating testbench|writing tb|scaffold/i },
  { id: "formal",      label: "Running formal checks", idx: 5,  re: /phase\s*3\.5|formal|sva|sby|symbiyosys/i },
  { id: "simulate",    label: "Simulating",            idx: 6,  re: /phase\s*4(?!\.5)|simulate|simulating|iverilog|verilator run/i },
  { id: "self_heal",   label: "Self-healing failures", idx: 7,  re: /phase\s*4\.5|self.?heal|auto.?fix|retry/i },
  { id: "fix_report",  label: "Writing fix report",    idx: 8,  re: /phase\s*5|fix report|summari[sz]e/i },
  { id: "done",        label: "Done",                  idx: 9,  re: /completed|passed|all done|verdict/i },
];

export function detectCurrentPhase(events = [], status = "") {
  // Walk the events latest-first; the latest matching phase wins.
  const list = Array.isArray(events) ? events : [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const e = list[i];
    const haystack = `${e.phase || ""} ${e.message || ""}`;
    for (let j = VERIFICATION_PHASES.length - 1; j >= 0; j -= 1) {
      const def = VERIFICATION_PHASES[j];
      if (def.re.test(haystack)) return def;
    }
  }
  // Fall back to status mapping when events haven't reported anything yet.
  const s = String(status || "").toLowerCase();
  if (s === "queued") return VERIFICATION_PHASES[0];
  if (s === "completed" || s === "passed") return VERIFICATION_PHASES[VERIFICATION_PHASES.length - 1];
  return null;
}

const REASONING_SENTINEL = /^\s*__(REASONING|DEBATE|THOUGHT)__\s*:\s*/i;

export function prettifyRunMessage(text) {
  if (text == null) return "";
  let out = String(text);
  // Strip leading sentinels the legacy agent used to wrap reasoning.
  out = out.replace(REASONING_SENTINEL, "");
  // Compress absolute paths (more than 3 segments) to .../last-two.
  out = out.replace(
    /(?:[A-Za-z]:)?(?:[/\\][^/\\\s]+){3,}([/\\][^/\\\s]+[/\\][^/\\\s]+)/g,
    "…$1",
  );
  return out.trim();
}

function buildStagesFromEvents(events, status) {
  const text = events
    .map((e) => `${e.phase || ""} ${e.message || ""}`)
    .join(" ")
    .toLowerCase();

  const stages = STAGE_DEFS.map((s) => ({ id: s.id, label: s.label, state: "todo" }));
  for (let i = 0; i < STAGE_DEFS.length; i += 1) {
    const def = STAGE_DEFS[i];
    if (def.match.some((m) => text.includes(m))) {
      stages[i].state = "done";
    }
  }

  const normalizedStatus = String(status || "").toLowerCase();
  const touched = stages.some((s) => s.state === "done");

  if (normalizedStatus === "completed" || normalizedStatus === "passed") {
    // Only fill remaining stages when we already saw pipeline progress in events.
    if (touched) {
      stages.forEach((s) => {
        if (s.state === "todo") s.state = "done";
      });
    } else if (stages.length) {
      stages[0].state = "done";
      if (stages.length > 1) stages[stages.length - 1].state = "bad";
    }
  } else if (normalizedStatus === "failed" || normalizedStatus === "cancelled") {
    // Mark last touched stage as bad, rest stay.
    for (let i = stages.length - 1; i >= 0; i -= 1) {
      if (stages[i].state === "done") {
        stages[i].state = "bad";
        break;
      }
    }
  } else if (status === "running") {
    // Mark the first todo as now.
    const firstTodo = stages.findIndex((s) => s.state === "todo");
    if (firstTodo >= 0) {
      stages[firstTodo].state = "now";
    }
  }

  return stages;
}
